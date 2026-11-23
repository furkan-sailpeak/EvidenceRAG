"""Model calls through any OpenAI-compatible API (Groq, OpenAI, Ollama, ...).

Three ways the pipeline calls a model: plain text, streamed text, and JSON
parsed into a pydantic model; plus one vision call used by `make ocr`.
`client`/`judge_client` are looked up at call time, so tests can patch them."""
import base64

from openai import OpenAI

from . import config


def _client(base_url, api_key):
    return OpenAI(base_url=base_url, api_key=api_key, max_retries=3) if base_url else None


client = _client(config.LLM_BASE_URL, config.LLM_API_KEY) if config.MODEL else None
judge_client = client
if client:
    print(f"LLM: {config.LLM_PROVIDER} ({config.LLM_BASE_URL}); answers={config.MODEL}, fast={config.FAST_MODEL}")
else:
    print("WARNING: no LLM configured (set LLM_PROVIDER and LLM_MODEL in .env); /api/chat will fail.")


def model_kwargs(model, provider=None):
    """Provider/model quirks, so callers never need to know them: gpt-oss emits
    hidden reasoning that counts against max_tokens; Groq can hide it and keep
    it short (faster first token, no empty replies)."""
    if "gpt-oss" in model and (provider or config.LLM_PROVIDER) == "groq":
        return {"reasoning_effort": "low", "extra_body": {"reasoning_format": "hidden"}}
    return {}


def _temperature(model, temperature, provider=None):
    """OpenAI reasoning models (gpt-5*, o-series) reject a temperature setting."""
    if (provider or config.LLM_PROVIDER) == "openai" and (model.startswith("gpt-5") or model[:2] in ("o1", "o3", "o4")):
        return {}
    return {"temperature": temperature}


def _target(judge):
    if judge:
        return judge_client, config.FAST_MODEL or config.MODEL
    return client, config.MODEL


def complete(messages, *, judge=False, temperature=0, **kwargs):
    """One non-streamed chat completion; returns the reply text."""
    llm, model = _target(judge)
    res = llm.chat.completions.create(
        model=model, messages=messages, **model_kwargs(model), **_temperature(model, temperature), **kwargs,
    )
    return res.choices[0].message.content or ""


def stream(messages, *, temperature=0):
    """Yield reply text chunks from the main model. If streaming fails before
    anything was produced, fall back to one non-streamed call."""
    started = False
    try:
        chunks = client.chat.completions.create(
            model=config.MODEL, messages=messages, stream=True,
            **model_kwargs(config.MODEL), **_temperature(config.MODEL, temperature),
        )
        for chunk in chunks:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                started = True
                yield delta
    except Exception as e:
        if started:
            raise
        print(f"Streaming failed, falling back to non-streaming: {e}")
        yield complete(messages, temperature=temperature)


def _response_format(schema, strict_schema):
    if not strict_schema:
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {
        "name": schema.model_config.get("title") or schema.__name__,
        "strict": True,
        "schema": schema.model_json_schema(),
    }}


def complete_json(prompt, schema, *, judge=True, temperature=0, strict_schema=False):
    """Ask for JSON and parse it into `schema` (a pydantic model). Raises on
    API errors and on replies that don't fit; callers decide the fallback.
    strict_schema=True sends the JSON schema for constrained decoding."""
    text = complete(
        [{"role": "user", "content": prompt}],
        judge=judge, temperature=temperature, response_format=_response_format(schema, strict_schema),
    )
    return schema.model_validate_json(text)


# --- Vision (used by `make ocr`) ----------------------------------------------------
_vision_client = None


def vision(prompt, png_bytes, *, as_json=False):
    """Send one page image + prompt to the vision model. Returns the reply text
    (JSON mode when as_json=True)."""
    global _vision_client
    if not config.VISION_MODEL:
        raise RuntimeError("No VISION_MODEL set (needed for page metadata or OCR_ENGINE=llm). See .env.example.")
    if _vision_client is None:
        _vision_client = _client(config.VISION_BASE_URL, config.VISION_API_KEY)
    image = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    res = _vision_client.chat.completions.create(
        model=config.VISION_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image}},
        ]}],
        **_temperature(config.VISION_MODEL, 0, config.VISION_PROVIDER),
        **({"response_format": {"type": "json_object"}} if as_json else {}),
    )
    return res.choices[0].message.content or ""
