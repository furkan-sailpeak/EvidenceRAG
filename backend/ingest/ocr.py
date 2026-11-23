"""`make ocr`: turn every file in data/pdfs/ into page text + page metadata.

For each page:
  1. text   -- the PDF's own text layer when it has one (exact, free, instant);
               otherwise OCR with OCR_ENGINE: tesseract (local, free) | google
               (Google Cloud Vision, best on old scans) | llm (the vision model).
  2. metadata -- if corpus.toml [metadata] defines fields/document types, the
               vision model labels the page (one call per page).

Output: data/ocr/<file_id>.json, saved every few pages, so an interrupted run
resumes where it stopped. FORCE=1 first moves existing results to
data/backups/ocr-<timestamp>/ and starts over; nothing is ever overwritten.

Usage (from backend/):  python -m ingest.ocr [--force] [--only-metadata] [--limit N]
"""
import argparse
import io
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from app import config, llm

FILE_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
OCR_ENGINE = (os.getenv("OCR_ENGINE") or "tesseract").lower()      # used for pages without a text layer
USE_TEXT_LAYER = (os.getenv("OCR_USE_TEXT_LAYER") or "1") == "1"
MIN_TEXT_CHARS = int((os.getenv("OCR_MIN_TEXT_CHARS") or "50"))      # fewer chars in the text layer = treat as a scan
DPI = int((os.getenv("OCR_DPI") or "300"))
WORKERS = int((os.getenv("OCR_WORKERS") or "4"))                      # pages processed in parallel
LANGUAGES = (os.getenv("OCR_LANGUAGES") or "eng")                     # tesseract language codes, e.g. "eng+nld"

PDF_DIR, OCR_DIR = Path(config.PDF_DIR), Path(config.OCR_DIR)
BACKUP_DIR = OCR_DIR.parent / "backups"


# --- Reading files ------------------------------------------------------------------
_open = {}


def _pdf(path):
    """The open PDF for `path` (one at a time; only used from the main thread)."""
    import pypdfium2 as pdfium
    if path not in _open:
        _open.clear()
        _open[path] = pdfium.PdfDocument(str(path))
    return _open[path]


def page_count(path):
    if path.suffix.lower() == ".pdf":
        return len(_pdf(path))
    from PIL import Image
    with Image.open(path) as img:
        return getattr(img, "n_frames", 1)


def pdf_info(path):
    if path.suffix.lower() != ".pdf":
        return {}
    try:
        meta = _pdf(path).get_metadata_dict()
        return {k.lower(): v for k, v in meta.items() if v and k in ("Title", "Author", "Subject", "CreationDate", "ModDate")}
    except Exception:
        return {}


def text_layer(path, page_number):
    if path.suffix.lower() != ".pdf":
        return ""
    text = _pdf(path)[page_number - 1].get_textpage().get_text_range()
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def render(path, page_number, dpi=DPI):
    """One page as a PIL image."""
    if path.suffix.lower() == ".pdf":
        return _pdf(path)[page_number - 1].render(scale=dpi / 72).to_pil()
    from PIL import Image
    img = Image.open(path)
    img.seek(page_number - 1)
    return img.convert("RGB")


def png(image, max_side=None):
    if max_side and max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def prepare_scan(image):
    """Grayscale + extra contrast: helps OCR on faded scans."""
    from PIL import ImageEnhance
    return ImageEnhance.Contrast(image.convert("L")).enhance(1.5)


# --- OCR engines ------------------------------------------------------------------------
def ocr_tesseract(image):
    try:
        import pytesseract
    except ImportError:
        sys.exit("OCR_ENGINE=tesseract needs: brew install tesseract (or apt install tesseract-ocr) and make setup")
    return pytesseract.image_to_string(prepare_scan(image), lang=LANGUAGES)


_vision_api = None


def ocr_google(image):
    global _vision_api
    try:
        from google.cloud import vision
    except ImportError:
        sys.exit("OCR_ENGINE=google needs: make install-google-ocr, and GOOGLE_APPLICATION_CREDENTIALS in .env")
    if _vision_api is None:
        _vision_api = vision.ImageAnnotatorClient()
    response = _vision_api.document_text_detection(image=vision.Image(content=png(prepare_scan(image))))
    if response.error.message:
        raise RuntimeError(response.error.message)
    return response.full_text_annotation.text if response.full_text_annotation else ""


OCR_PROMPT = ("Transcribe all text on this page exactly as written, in reading order. "
              "Keep line breaks. Do not summarize, translate or add anything. "
              "Write [illegible] for text you cannot read. Reply with the text only.")


def ocr_llm(image):
    return llm.vision(OCR_PROMPT, png(image, max_side=2000))


ENGINES = {"tesseract": ocr_tesseract, "google": ocr_google, "llm": ocr_llm}


# --- Page metadata ----------------------------------------------------------------------
FIELDS = config.CORPUS["metadata"]["fields"]
DOC_TYPES = config.CORPUS["metadata"]["document_types"]


def metadata_prompt():
    lines = [f'  "{f["name"]}": true or false  -- {f["description"]}' for f in FIELDS]
    if DOC_TYPES:
        lines.append(f'  "document_type": one of {json.dumps(DOC_TYPES)}  -- the kind of document this page belongs to')
    return ("Look at this document page and return a JSON object with exactly these keys:\n{\n"
            + ",\n".join(lines) + "\n}\nAnswer from what is visible on the page. Reply with the JSON only.")


def page_metadata(image):
    reply = json.loads(llm.vision(metadata_prompt(), png(image, max_side=1600), as_json=True))
    out = {f["name"]: bool(reply.get(f["name"])) if isinstance(reply.get(f["name"]), bool) else None for f in FIELDS}
    if DOC_TYPES:
        dt = str(reply.get("document_type", "")).strip().lower()
        out["document_type"] = next((t for t in DOC_TYPES if t.lower() == dt), None)
    return out


# --- One page / one file ------------------------------------------------------------------
def prepare_page(path, page_number, existing, only_metadata):
    """Main thread (pdfium is not thread-safe): read the text layer and render
    the image the API calls will need. Returns (page dict, image or None, needs_ocr)."""
    page = dict(existing or {"page": page_number})
    needs_ocr = False
    if not only_metadata and "text" not in page:
        text = text_layer(path, page_number) if USE_TEXT_LAYER else ""
        if len(text) >= MIN_TEXT_CHARS:
            page.update(text=text, engine="text-layer")
        else:
            needs_ocr = True
    needs_meta = bool(FIELDS or DOC_TYPES) and page.get("metadata") is None
    image = render(path, page_number, dpi=DPI if needs_ocr else 150) if (needs_ocr or needs_meta) else None
    return page, image, needs_ocr


def finish_page(page, image, needs_ocr):
    """Worker thread: the slow OCR / vision-model calls."""
    if needs_ocr:
        page.update(text=ENGINES[OCR_ENGINE](image).strip(), engine=OCR_ENGINE)
    if (FIELDS or DOC_TYPES) and page.get("metadata") is None:
        try:
            page["metadata"] = page_metadata(image)
        except Exception as e:
            print(f"    ! metadata failed on page {page['page']}: {e}")
    return page


def save(out_path, doc):
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    os.replace(tmp, out_path)


def process_file(path, only_metadata):
    out_path = OCR_DIR / f"{path.stem}.json"
    doc = json.loads(out_path.read_text()) if out_path.exists() else None
    n = page_count(path)
    if doc is None:
        if only_metadata:
            return "skipped (no OCR yet)"
        doc = {"file": path.name, "file_id": path.stem, "number_of_pages": n, "info": pdf_info(path), "pages": []}
    pages = {p["page"]: p for p in doc["pages"]}
    todo = [i for i in range(1, n + 1)
            if (not only_metadata and "text" not in pages.get(i, {}))
            or ((FIELDS or DOC_TYPES) and pages.get(i, {}).get("metadata") is None)]
    if not todo:
        return "done"
    with ThreadPoolExecutor(WORKERS) as pool:
        for start in range(0, len(todo), WORKERS):          # a batch at a time keeps memory flat
            batch = todo[start:start + WORKERS]
            futures = {i: pool.submit(finish_page, *prepare_page(path, i, pages.get(i), only_metadata)) for i in batch}
            for i in batch:
                try:
                    pages[i] = futures[i].result()
                except Exception as e:
                    print(f"    ! page {i} failed: {e}")
            doc["pages"] = [pages[k] for k in sorted(pages)]
            save(out_path, doc)   # after every batch: a crash loses at most one batch
    missing = n - sum(1 for p in pages.values() if "text" in p)
    return f"{len(todo)} page(s) processed" + (f", {missing} still missing (re-run make ocr)" if missing else "")


def backup():
    if OCR_DIR.exists() and any(OCR_DIR.glob("*.json")):
        dest = BACKUP_DIR / f"ocr-{datetime.now():%Y%m%d-%H%M%S}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(OCR_DIR), str(dest))
        print(f"Backed up previous results to {dest}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="back up existing results, then redo everything")
    p.add_argument("--only-metadata", action="store_true", help="fill missing page metadata, keep the text")
    p.add_argument("--limit", type=int, help="only the first N files (to try things out)")
    args = p.parse_args()

    if OCR_ENGINE not in ENGINES:
        sys.exit(f"OCR_ENGINE={OCR_ENGINE!r}: use one of {', '.join(ENGINES)}")
    files = sorted(f for f in PDF_DIR.iterdir() if f.suffix.lower() in FILE_TYPES) if PDF_DIR.exists() else []
    if not files:
        sys.exit(f"No files in {PDF_DIR}. Put your PDFs (or scans: png/jpg/tif) there first.")
    if args.force:
        backup()
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    files = files[: args.limit] if args.limit else files

    meta = f"metadata: {len(FIELDS)} field(s){' + document type' if DOC_TYPES else ''} via {config.VISION_MODEL}" \
        if (FIELDS or DOC_TYPES) else "metadata: none defined in corpus.toml"
    print(f"{len(files)} file(s) | text layer: {'on' if USE_TEXT_LAYER else 'off'} | OCR engine for scans: {OCR_ENGINE} | {meta}")
    t0 = time.time()
    for k, path in enumerate(files, 1):
        try:
            status = process_file(path, args.only_metadata)
        except Exception as e:
            status = f"FAILED: {e}"
        print(f"[{k}/{len(files)}] {path.name}: {status}")
    print(f"Finished in {(time.time() - t0) / 60:.1f} min. Results in {OCR_DIR}. Next: make ingest")


if __name__ == "__main__":
    main()
