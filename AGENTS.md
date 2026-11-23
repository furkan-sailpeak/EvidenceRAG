# AGENTS.md

Guidance for coding agents (and humans) working on EvidenceRAG. Read README.md first for what the
project does; this file covers how to change it safely.

## What this is

A generic RAG app over any PDF collection: `make ocr` → `make ingest` → `make embed` → a Flask API
plus a React chat UI that answers with page-level citations. Everything about one collection lives in
`corpus.toml`; the code and prompts must stay collection-agnostic.

## Layout

| Path | Contents |
|---|---|
| `corpus.toml` / `corpus.example.toml` | The collection config (template with TODOs / filled-in example used by the tests) |
| `backend/app/` | API (`main.py`), answer pipeline (`pipeline.py`), router and generation, retrieval, verification, `config.py` (all settings), `corpus.py` (corpus schema + validation), `llm.py` (all model calls), `embeddings.py` |
| `backend/prompts/*.j2` | Jinja2 prompt templates; collection details come from `{{ corpus.* }}` |
| `backend/ingest/` | `ocr.py` (make ocr/metadata), `load.py` (make ingest), `embed.py` (make embed), `status.py` |
| `backend/tests/` | Offline pytest suite |
| `frontend/` | React + Vite chat UI; reads its labels from `GET /api/corpus` |
| `setup/corpus_wizard.py` | Streamlit form that writes `corpus.toml` (make configure) |
| `eval/` | Evaluation datasets, editor and LangSmith runner |

## Commands

Use the Makefile; `make` lists everything.

- `make test`: run the tests (offline; no keys, models or database needed)
- `make check`: pre-commit (ruff, whitespace, yaml/toml) + tests + frontend eslint. Run before finishing a change.
- `make dev`: backend on :5001 and frontend on :5173
- Pipeline steps: `make db`, `make ocr`, `make ingest`, `make embed`, `make status`

## Rules

1. **No collection-specific text outside `corpus.toml`.** Prompts, code, UI strings and tests must not
   mention a particular domain. `backend/tests/test_corpus.py` renders every prompt and fails on known
   domain terms; if you need a new collection-specific value, add it to the corpus schema instead.
2. **Changing the corpus schema touches five places, together:** `SCHEMA` (and validation) in
   `backend/app/corpus.py`, `corpus.toml` (template, with a comment and an example per key),
   `corpus.example.toml`, `setup/corpus_wizard.py`, and the tests. `test_template_and_example_have_the_same_keys` catches drift.
3. **Tests never touch the network.** `conftest.py` blanks keys and sets `llm.client = None`; use the
   `llm_replies` / `failing_llm` / `mock_db` fixtures. Tests run against `corpus.example.toml`.
4. **Settings go in `backend/app/config.py`**, read from `.env`, where an empty value means "use the default"
   (`os.getenv("X") or default`). Document new settings in `.env.example` (comments on their own lines).
5. **All model calls go through `backend/app/llm.py`** so provider quirks stay in one place. Every provider is
   reached through the OpenAI-compatible API; do not add provider-specific SDKs.
6. **Never destroy user data.** `make ocr` is resumable and moves old results to `data/backups/` before redoing
   them. The `pages` table is derived data (rebuilt by `make ingest`); `data/pdfs/` and `data/ocr/` are not.
7. **SQL identifiers** (the `pages` table, metadata column names) are interpolated into SQL only after
   validation in `corpus.py`. Values always go through query parameters.
8. **Answer contracts the code relies on:** the exact refusal line `config.REFUSAL_LINE`, citations as `[N]`
   pointing at `<source id="N">` blocks, and `make embed` using the same embedding model as query time.
9. **Keep it simple and explanatory.** Every make target has a one-line `##` description; user-facing messages
   say what to do next. Python style: ruff, line length 120.

## Before you finish

`make check` passes; if you changed prompts, `make eval` on a dataset shows no drop beyond run-to-run
noise (a few points).
