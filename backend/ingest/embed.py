"""`make embed`: add a vector embedding to every page (the meaning-based half
of search) and build the vector index.

Resumable: only pages without an embedding are processed. If EMBED_PROVIDER /
EMBED_MODEL changed since the last run, all embeddings are rebuilt (the app
must embed queries with the same model the pages were embedded with).

Usage (from backend/):  python -m ingest.embed
"""
import time

from psycopg2.extras import execute_values

from app import config, db, embeddings

T = config.PAGES_TABLE
BATCH = 64


def stored_model(cur):
    cur.execute("""SELECT col_description(c.oid, a.attnum) FROM pg_class c
                   JOIN pg_attribute a ON a.attrelid = c.oid
                   WHERE c.relname = %s AND a.attname = 'embedding'""", (T,))
    row = cur.fetchone()
    return row[0] if row else None


def main():
    model = f"{config.EMBED_PROVIDER}:{config.EMBED_MODEL}"
    dim = len(embeddings.embed(["dimension probe"])[0])
    with db.cursor() as cur:
        cur.execute(f"SELECT to_regclass('{T}')")
        if cur.fetchone()[0] is None:
            raise SystemExit("No pages table yet. Run make ingest first.")
        previous = stored_model(cur)
        if previous != model:
            if previous:
                print(f"Embedding model changed ({previous} -> {model}): rebuilding all embeddings.")
            cur.execute(f"ALTER TABLE {T} DROP COLUMN IF EXISTS embedding")
            cur.execute(f"ALTER TABLE {T} ADD COLUMN embedding halfvec({dim})")
            cur.execute(f"COMMENT ON COLUMN {T}.embedding IS %s", (model,))
        cur.execute(f"SELECT COUNT(*) FROM {T} WHERE embedding IS NULL AND length(trim(content)) > 0")
        todo = cur.fetchone()[0]
    print(f"{todo} page(s) to embed with {model} ({dim} dimensions)")

    done, t0 = 0, time.time()
    while True:
        with db.cursor() as cur:
            cur.execute(f"""SELECT id, content FROM {T}
                            WHERE embedding IS NULL AND length(trim(content)) > 0 ORDER BY id LIMIT %s""", (BATCH,))
            batch = cur.fetchall()
        if not batch:
            break
        vectors = embeddings.embed([content for _, content in batch])
        with db.cursor() as cur:
            execute_values(cur, f"UPDATE {T} AS t SET embedding = v.e::halfvec FROM (VALUES %s) AS v(id, e) WHERE t.id = v.id",
                           [(pid, "[" + ",".join(f"{x:.6f}" for x in vec) + "]") for (pid, _), vec in zip(batch, vectors)])
        done += len(batch)
        print(f"  {done}/{todo} ({done / max(time.time() - t0, 1e-9):.0f} pages/s)", end="\r")

    print("\nBuilding the vector index...")
    with db.cursor() as cur:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {T}_embedding_hnsw ON {T} USING hnsw (embedding halfvec_cosine_ops)")
    print("Done. The app is ready: make dev (local) or make up (Docker).")


if __name__ == "__main__":
    main()
