"""Text embeddings for the vector half of search, shared by the app (queries)
and `make embed` (pages), so both always use the same model.

EMBED_PROVIDER: local (fastembed on CPU, default) | openai | ollama."""
import threading

from . import config

# bge models are trained with this instruction on the query side only.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_lock = threading.Lock()
_model = None


def _query_prefix():
    return _QUERY_PREFIX if "bge" in config.EMBED_MODEL.lower() else ""


def _local():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding
                _model = TextEmbedding(config.EMBED_MODEL, cache_dir=config.FASTEMBED_CACHE_PATH)
    return _model


def _api_embed(texts):
    from openai import OpenAI
    if config.EMBED_PROVIDER == "ollama":
        api = OpenAI(base_url=config.PROVIDERS["ollama"][0], api_key="not-needed")
    else:
        import os
        api = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    res = api.embeddings.create(model=config.EMBED_MODEL, input=texts)
    return [d.embedding for d in res.data]


def embed(texts, *, query=False):
    """Embed a list of texts. Returns list[list[float]]."""
    texts = [((_query_prefix() if query else "") + (t or ""))[:8000] for t in texts]
    if config.EMBED_PROVIDER == "local":
        return [v.tolist() for v in _local().embed(texts)]
    if config.EMBED_PROVIDER in ("openai", "ollama"):
        return _api_embed(texts)
    raise ValueError(f"EMBED_PROVIDER={config.EMBED_PROVIDER!r}: use local, openai or ollama")


def embed_query(text):
    """One query vector, or None on failure (search then runs on keywords only)."""
    if not text or not text.strip():
        return None
    try:
        return embed([text[:2000]], query=True)[0]
    except Exception as e:
        print(f"[rag] WARNING: query embedding failed, vector search skipped for this query: {e}")
        return None
