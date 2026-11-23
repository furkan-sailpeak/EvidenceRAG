"""Flask app: HTTP routes only. The chat logic lives in pipeline.py.

Run locally:  make dev               (backend on :5001, frontend on :5173)
Production:   gunicorn --config gunicorn.conf.py app.main:app
"""
import os
import threading
import time

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, stream_with_context
from flask_cors import CORS

from . import config, db, llm, pipeline, prompts
from .generation import sanitize_history

_FRONTEND_DIST = os.path.join(config.REPO_ROOT, "frontend", "dist")   # built by `make build` / Docker
app = Flask(__name__, static_folder=_FRONTEND_DIST, static_url_path="/")

if config.ALLOWED_ORIGINS:
    CORS(app, resources={r"/api/*": {"origins": config.ALLOWED_ORIGINS}})
    print(f"CORS locked to: {config.ALLOWED_ORIGINS}")
else:
    CORS(app)
    print("WARNING: ALLOWED_ORIGINS unset — CORS is open to all origins (dev mode).")


@app.route('/api/corpus', methods=['GET'])
def corpus_route():
    """What the frontend shows: the [app] section of corpus.toml."""
    return jsonify({**config.CORPUS["app"], "document_noun": config.CORPUS["collection"]["document_noun"]})


@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json(silent=True) or {}
    query = data.get('query')
    if not query or not isinstance(query, str) or not query.strip():
        return jsonify({"error": "No query provided"}), 400
    query = query.strip()[:config.MAX_QUERY_CHARS]
    if not llm.client:
        return jsonify({"error": "LLM client not configured"}), 500

    history = sanitize_history(data.get('history'))
    return Response(
        stream_with_context(pipeline.run(query, history)),
        mimetype='text/event-stream',
        headers={
            "Cache-Control": "no-cache",
            # Stop intermediate proxies buffering the SSE stream.
            "X-Accel-Buffering": "no",
        },
    )


# --- Collection stats ----------------------------------------------------------------
# The collection only changes on `make ingest`, so the counts are cached.
_stats = {"ts": 0, "data": None}
_STATS_TTL = int((os.getenv("STATS_TTL") or "3600"))
_stats_lock = threading.Lock()


def compute_full_stats():
    """Page/document totals, one count per yes/no metadata field, and the most
    common document types. Field names come from corpus.toml (validated)."""
    fields = config.CORPUS["metadata"]["fields"]
    counts = "".join(f", COUNT(*) FILTER (WHERE {f['name']})" for f in fields)
    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT file_id){counts} FROM {config.PAGES_TABLE}")
        total_pages, total_docs, *field_counts = cur.fetchone()
        doc_types = []
        if config.CORPUS["metadata"]["document_types"]:
            cur.execute(f"""
                SELECT document_type, COUNT(*) FROM {config.PAGES_TABLE}
                WHERE document_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5
            """)
            doc_types = cur.fetchall()
    return {
        "total_pages": total_pages, "total_docs": total_docs,
        "fields": [{"name": f["name"], "description": f["description"], "pages": n} for f, n in zip(fields, field_counts)],
        "document_types": [{"type": t, "count": n} for t, n in doc_types],
    }


def _stats_fresh():
    return _stats["data"] and time.time() - _stats["ts"] < _STATS_TTL


@app.route('/api/stats', methods=['GET'])
def stats_route():
    if _stats_fresh():
        return jsonify(_stats["data"])
    try:
        with _stats_lock:
            # Re-check inside the lock so a cold-cache traffic spike runs the
            # scan once, not once per concurrent visitor.
            if not _stats_fresh():
                _stats.update({"ts": time.time(), "data": compute_full_stats()})
        return jsonify(_stats["data"])
    except Exception as e:
        print(f"[stats] refresh failed: {e}")
        # Serve stale rather than erroring — the numbers are cosmetic.
        if _stats["data"]:
            return jsonify(_stats["data"])
        return jsonify({"error": "stats unavailable"}), 503


# --- Other routes -----------------------------------------------------------------
@app.route('/api/analyze', methods=['POST'])
def analyze():
    """Toolbar actions on an answer: list the names in it, or summarise it."""
    data = request.get_json(silent=True) or {}
    action, text = data.get('action'), data.get('text')
    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "No text provided"}), 400
    text = text[:config.MAX_ANALYZE_CHARS]
    if not llm.client:
        return jsonify({"error": "LLM client not configured"}), 500
    templates = {"names": "analyze-names.j2", "summarize": "analyze-summarize.j2"}
    if action not in templates:
        return jsonify({"error": "Invalid action"}), 400
    try:
        result = llm.complete([{"role": "user", "content": prompts.render(templates[action], text=text)}], temperature=0.3)
        return jsonify({"result": result})
    except Exception as e:
        # Provider/driver messages carry org ids, quota numbers, DB hosts and
        # users: log them, never return them.
        print(f"[analyze] failed: {e!r}")
        return jsonify({"error": "Analysis failed. Please try again later."}), 500


@app.route('/api/source/<path:filename>', methods=['GET'])
def source(filename):
    """Open a cited document: the public copy if corpus.toml sets source_url,
    otherwise the PDF from data/pdfs (send_from_directory blocks path tricks)."""
    if config.SOURCE_URL:
        return redirect(config.SOURCE_URL.format(file_id=os.path.splitext(filename)[0]))
    return send_from_directory(config.PDF_DIR, filename)


@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')


@app.errorhandler(404)
def not_found(e):
    if not request.path.startswith('/api'):
        return send_from_directory(app.static_folder, 'index.html')
    return jsonify({"error": "Not found"}), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
