"""All settings in one place. Importing this module loads .env.

What the collection is (names, scope, examples, metadata fields) lives in
corpus.toml; this file holds the technical settings: models, providers,
database and retrieval tuning. Every value can be overridden in .env."""
import os
import re

from dotenv import load_dotenv

from . import corpus as _corpus

REPO_ROOT = _corpus.REPO_ROOT
load_dotenv(os.path.join(REPO_ROOT, ".env"))


def _flag(name, default="0"):
    return (os.getenv(name) or default).lower() in ("1", "true", "yes")


# --- Corpus -----------------------------------------------------------------------
CORPUS = _corpus.load()
PAGES_TABLE = "pages"
METADATA_COLUMNS = set(CORPUS["metadata"]["columns"])
DOC_ID = re.compile(CORPUS["documents"]["id_pattern"]) if CORPUS["documents"]["id_pattern"] else None
SOURCE_URL = CORPUS["documents"]["source_url"]     # "" = serve PDFs from PDF_DIR
PDF_DIR = (os.getenv("PDF_DIR") or os.path.join(_corpus.REPO_ROOT, "data", "pdfs"))
OCR_DIR = (os.getenv("OCR_DIR") or os.path.join(_corpus.REPO_ROOT, "data", "ocr"))

# --- Database -----------------------------------------------------------------------
DATABASE_URL = (os.getenv("DATABASE_URL") or "postgresql://rag:rag@localhost:5433/rag")   # the make db container
DB_POOL_MIN = int((os.getenv("DB_POOL_MIN") or "1"))
DB_POOL_MAX = int((os.getenv("DB_POOL_MAX") or "8"))
DB_POOL_WAIT_S = float((os.getenv("DB_POOL_WAIT_S") or "5"))

# --- Models -------------------------------------------------------------------------
# Any OpenAI-compatible API. LLM_PROVIDER picks a known endpoint and default models;
# LLM_BASE_URL points anywhere else (vLLM, LM Studio, Together, a proxy, ...).
#   groq   -- fast and cheap hosted open models (default). Needs GROQ_API_KEY.
#   openai -- needs OPENAI_API_KEY and explicit model names.
#   ollama -- local, free, private; needs Ollama running and the models pulled.
# Providers retire models: `make models` lists what yours offers today.
PROVIDERS = {
    #          base URL                              API key env       answers                fast tasks            vision (OCR/metadata)
    "groq":   ("https://api.groq.com/openai/v1", "GROQ_API_KEY",   "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"),
    "openai": ("https://api.openai.com/v1",      "OPENAI_API_KEY", "",                    "",                   ""),
    "ollama": ("http://localhost:11434/v1",      "",               "qwen2.5:7b",          "qwen2.5:3b",         "qwen2.5vl:7b"),
}


def _provider(name, base_url, api_key):
    """(name, base_url, api_key, default models) for one provider setting."""
    name = (name or "groq").lower()
    if name not in PROVIDERS and not base_url:
        raise ValueError(f"provider {name!r}: use one of {', '.join(PROVIDERS)}, or set a base URL")
    url, key_env, *models = PROVIDERS.get(name, ("", "", "", "", ""))
    key = api_key or (os.getenv(key_env) if key_env else "") or "not-needed"
    return name, base_url or url, key, models


LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, _defaults = _provider(
    os.getenv("LLM_PROVIDER"), os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"))
MODEL = os.getenv("LLM_MODEL") or _defaults[0]            # writes the answers
FAST_MODEL = os.getenv("LLM_FAST_MODEL") or _defaults[1]  # router, reranker, judges: small JSON tasks

# Vision model for `make ocr` (page metadata, and OCR when OCR_ENGINE=llm).
# Same provider as above unless VISION_PROVIDER is set.
if os.getenv("VISION_PROVIDER"):
    VISION_PROVIDER, VISION_BASE_URL, VISION_API_KEY, _vdefaults = _provider(
        os.getenv("VISION_PROVIDER"), os.getenv("VISION_BASE_URL"), os.getenv("VISION_API_KEY"))
else:
    VISION_PROVIDER, VISION_BASE_URL, VISION_API_KEY, _vdefaults = LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, _defaults
VISION_MODEL = os.getenv("VISION_MODEL") or _vdefaults[2]
# 0 = the same question gets the same answer (the collection does not change).
GEN_TEMPERATURE = float((os.getenv("GEN_TEMPERATURE") or "0"))

# --- Embeddings -----------------------------------------------------------------------
#   local  -- fastembed on CPU, no API, no cost (default; good for English).
#   openai -- OpenAI embeddings (needs OPENAI_API_KEY).
#   ollama -- e.g. nomic-embed-text, through the Ollama server.
# Changing provider or model means re-running `make embed`.
EMBED_PROVIDER = (os.getenv("EMBED_PROVIDER") or "local").lower()
EMBED_MODEL = os.getenv("EMBED_MODEL") or {
    "local": "BAAI/bge-small-en-v1.5", "openai": "text-embedding-3-small", "ollama": "nomic-embed-text"}.get(EMBED_PROVIDER, "")
FASTEMBED_CACHE_PATH = (os.getenv("FASTEMBED_CACHE_PATH") or os.path.join(_corpus.REPO_ROOT, "data", "models"))

# --- Web --------------------------------------------------------------------------------
# Lock the API to your own domain(s) in production; empty = any origin (local use).
ALLOWED_ORIGINS = [o.strip() for o in (os.getenv("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
# Caps on anything that reaches a model call.
MAX_QUERY_CHARS = int((os.getenv("MAX_QUERY_CHARS") or "2000"))
MAX_HISTORY_MSGS = int((os.getenv("MAX_HISTORY_MSGS") or "20"))
MAX_HISTORY_CHARS = int((os.getenv("MAX_HISTORY_CHARS") or "4000"))
MAX_ANALYZE_CHARS = int((os.getenv("MAX_ANALYZE_CHARS") or "20000"))
# Cosmetic pacing when the checked answer is streamed to the browser (0 = off).
REPLAY_BUDGET_S = float((os.getenv("REPLAY_BUDGET_S") or "1.5"))
REPLAY_MAX_DELAY_S = float((os.getenv("REPLAY_MAX_DELAY_S") or "0.015"))

# --- Retrieval tuning ---------------------------------------------------------------------
FTS_MIN_HITS = int((os.getenv("FTS_MIN_HITS") or "10"))        # fewer AND-matches -> retry with OR
ILIKE_TIMEOUT_MS = int((os.getenv("ILIKE_TIMEOUT_MS") or "4000"))
HNSW_EF_SEARCH = int((os.getenv("HNSW_EF_SEARCH") or "100"))   # vector recall vs speed
CONTEXT_LIMIT_SIMPLE = 15                                   # pages given to the model after reranking
CONTEXT_LIMIT_RESEARCH = 20
MAX_SEARCH_TERMS = int((os.getenv("MAX_SEARCH_TERMS") or "6"))
MAX_ENTITY_BRIEFS = int((os.getenv("MAX_ENTITY_BRIEFS") or "3"))
DOC_CONTEXT_CHARS = int((os.getenv("DOC_CONTEXT_CHARS") or "120000"))  # cap for whole-document lookups

# --- Verification --------------------------------------------------------------------------
JUDGE_SOURCE_CHARS = int((os.getenv("JUDGE_SOURCE_CHARS") or "6000"))   # page text shown to the checkers
VERIFY_ANSWER_CHARS = int((os.getenv("VERIFY_ANSWER_CHARS") or "8000"))

# The exact refusal line; prompts, the out-of-scope route and the checks match on it.
REFUSAL_LINE = "The retrieved documents do not contain sufficient information to answer this query."
