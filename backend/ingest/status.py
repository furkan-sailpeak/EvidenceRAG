"""`make status`: how far along each step is, and what to run next."""
import json
from pathlib import Path

from app import config, db
from ingest.ocr import FILE_TYPES


def main():
    pdf_dir, ocr_dir = Path(config.PDF_DIR), Path(config.OCR_DIR)
    files = [f for f in pdf_dir.iterdir() if f.suffix.lower() in FILE_TYPES] if pdf_dir.exists() else []
    results = list(ocr_dir.glob("*.json")) if ocr_dir.exists() else []
    pages = [p for r in results for p in json.loads(r.read_text())["pages"]]
    total = sum(json.loads(r.read_text())["number_of_pages"] for r in results)
    with_text = sum("text" in p for p in pages)
    wants_meta = bool(config.CORPUS["metadata"]["fields"] or config.CORPUS["metadata"]["document_types"])
    with_meta = sum(p.get("metadata") is not None for p in pages)
    engines = {}
    for p in pages:
        engines[p.get("engine")] = engines.get(p.get("engine"), 0) + 1

    print(f"corpus.toml   OK ({config.CORPUS['app']['name']})")
    print(f"files         {len(files)} in {pdf_dir}")
    print(f"make ocr      {len(results)}/{len(files)} files, {with_text}/{total} pages read"
          + (f" ({', '.join(f'{n} {e}' for e, n in engines.items())})" if engines else ""))
    if wants_meta:
        print(f"  metadata    {with_meta}/{total} pages labelled")
    db_pages = embedded = None
    try:
        with db.cursor() as cur:
            cur.execute(f"SELECT to_regclass('{config.PAGES_TABLE}')")
            if cur.fetchone()[0]:
                cur.execute(f"SELECT COUNT(*) FROM {config.PAGES_TABLE}")
                db_pages = cur.fetchone()[0]
                cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = 'embedding'",
                            (config.PAGES_TABLE,))
                if cur.fetchone():
                    cur.execute(f"SELECT COUNT(*) FROM {config.PAGES_TABLE} WHERE embedding IS NOT NULL")
                    embedded = cur.fetchone()[0]
        print(f"make ingest   {db_pages if db_pages is not None else 0} pages in the database")
        print(f"make embed    {embedded or 0}/{db_pages or 0} pages embedded")
    except Exception as e:
        print(f"database      not reachable ({str(e).splitlines()[0]}). Start it with: make db")

    if not files:
        step = f"put your PDFs (or scans) in {pdf_dir}"
    elif len(results) < len(files) or with_text < total or (wants_meta and with_meta < total):
        step = "make ocr"
    elif db_pages is None or db_pages < with_text:
        step = "make ingest"
    elif (embedded or 0) < db_pages:
        step = "make embed"
    else:
        step = "make dev (try it locally) or make up (Docker)"
    print(f"\nNext: {step}")


if __name__ == "__main__":
    main()
