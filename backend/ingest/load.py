"""`make ingest`: (re)build the `pages` table from data/ocr/*.json.

One row per page: text, file/page numbers, and one column per metadata field
from corpus.toml. The table is derived data: rebuilding it from data/ocr is
always safe. Embeddings are added afterwards by `make embed`.

Usage (from backend/):  python -m ingest.load
"""
import json
import sys
from pathlib import Path

from psycopg2.extras import execute_values

from app import config, db

T = config.PAGES_TABLE
FIELDS = [f["name"] for f in config.CORPUS["metadata"]["fields"]]
HAS_TYPES = bool(config.CORPUS["metadata"]["document_types"])


def create_table(cur):
    extra = "".join(f", {name} BOOLEAN" for name in FIELDS) + (", document_type TEXT" if HAS_TYPES else "")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute(f"DROP TABLE IF EXISTS {T}")
    cur.execute(f"""
        CREATE TABLE {T} (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            number_of_pages INTEGER,
            ocr_engine TEXT,
            content TEXT{extra}
        )""")


def rows():
    files = sorted(Path(config.OCR_DIR).glob("*.json"))
    if not files:
        sys.exit(f"No OCR results in {config.OCR_DIR}. Run make ocr first.")
    for path in files:
        doc = json.loads(path.read_text())
        for page in doc["pages"]:
            if "text" not in page:
                continue
            meta = page.get("metadata") or {}
            yield (doc["file_id"], doc["file"], page["page"], doc["number_of_pages"], page.get("engine"), page["text"],
                   *[meta.get(name) for name in FIELDS], *([meta.get("document_type")] if HAS_TYPES else []))


def main():
    cols = ["file_id", "filename", "page_number", "number_of_pages", "ocr_engine", "content", *FIELDS,
            *(["document_type"] if HAS_TYPES else [])]
    data = list(rows())
    with db.cursor() as cur:
        create_table(cur)
        execute_values(cur, f"INSERT INTO {T} ({', '.join(cols)}) VALUES %s", data, page_size=500)
        print("Building indexes (full-text search, file lookup)...")
        cur.execute(f"CREATE INDEX ON {T} USING GIN (to_tsvector('english', content))")
        cur.execute(f"CREATE INDEX ON {T} (file_id, page_number)")
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT file_id), COUNT(*) FILTER (WHERE length(trim(content)) = 0) FROM {T}")
        pages, docs, empty = cur.fetchone()
    print(f"Loaded {pages} pages from {docs} documents into '{T}'"
          + (f" ({empty} pages have no text)" if empty else "") + ". Next: make embed")


if __name__ == "__main__":
    main()
