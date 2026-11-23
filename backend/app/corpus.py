"""Load and validate corpus.toml (everything about the document collection).

The prompts read it as {{ corpus.* }}, the frontend gets the [app] section from
/api/corpus, and the ingest scripts read [metadata]. A missing value, a value
still set to "TODO", or a malformed one stops the app at startup with a list
of what to fix, instead of producing a prompt with a hole in it."""
import os
import re
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_FILE = os.path.join(REPO_ROOT, "corpus.toml")

# section -> {key: type}; every key is required (some may be empty, see OPTIONAL_EMPTY).
SCHEMA = {
    "app": {"name": str, "tagline": str, "welcome": str, "input_placeholder": str,
            "example_questions": list, "footer": str},
    "collection": {"name": str, "document_noun": str, "vocabulary": str, "persona": str},
    "scope": {"in_scope": str},
    "examples": {"keywords": list, "followup": dict},
    "background": {"enabled": bool, "context": str, "examples": list},
    "documents": {"id_pattern": str, "id_example": str, "source_url": str},
    "metadata": {"document_types": list, "fields": list},
}
OPTIONAL_EMPTY = {"app.footer", "background.context", "background.examples", "documents.id_pattern",
                  "documents.id_example", "documents.source_url", "metadata.document_types", "metadata.fields"}

# Columns of the pages table; metadata fields must not reuse them.
RESERVED_COLUMNS = {"id", "file_id", "filename", "page_number", "number_of_pages", "content",
                    "ocr_engine", "embedding", "document_type"}
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class CorpusError(ValueError):
    pass


def load(path=None):
    path = path or os.getenv("CORPUS_FILE") or DEFAULT_FILE
    if not os.path.isfile(path):
        raise CorpusError(f"{path} not found (set CORPUS_FILE, or fill in corpus.toml in the repo root).")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    validate(data, os.path.basename(path))
    data["metadata"]["columns"] = metadata_columns(data)
    return data


def metadata_columns(data):
    """Columns the metadata step fills and the router may filter on."""
    cols = [f["name"] for f in data["metadata"]["fields"]]
    return cols + (["document_type"] if data["metadata"]["document_types"] else [])


def _todos(value, where=""):
    if isinstance(value, str):
        return [where] if "TODO" in value else []
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _todos(v, f"{where}[{i}]")]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _todos(v, f"{where}.{k}" if where else k)]
    return []


def validate(data, name="corpus.toml"):
    problems = []
    for section, keys in SCHEMA.items():
        values = data.get(section)
        if not isinstance(values, dict):
            problems.append(f"missing section [{section}]")
            continue
        for key, typ in keys.items():
            where = f"{section}.{key}"
            if key not in values:
                problems.append(f"missing {where}")
            elif not isinstance(values[key], typ):
                problems.append(f"{where} must be a {typ.__name__}")
            elif where not in OPTIONAL_EMPTY and typ in (str, list) and not values[key]:
                problems.append(f"{where} is empty")
    if problems:
        raise CorpusError(f"{name}: " + "; ".join(problems))

    todos = _todos(data)
    if todos:
        raise CorpusError(f"{name}: replace the TODO values in " + ", ".join(todos))

    ex = data["examples"]
    if not all(isinstance(k, dict) and isinstance(k.get("question"), str) and isinstance(k.get("terms"), list)
               for k in ex["keywords"]):
        problems.append('examples.keywords entries need question = "..." and terms = ["..."]')
    if not all(isinstance(ex["followup"].get(k), str) and ex["followup"][k] for k in ("previous", "question", "rewritten")):
        problems.append("examples.followup needs previous, question and rewritten")
    bg = data["background"]
    if bg["enabled"] and not (bg["context"] and bg["examples"]):
        problems.append("background.enabled = true needs a context and examples")

    docs = data["documents"]
    if docs["id_pattern"]:
        try:
            if re.compile(docs["id_pattern"]).groups != 1:
                problems.append("documents.id_pattern needs exactly one (group)")
            elif not re.search(docs["id_pattern"], docs["id_example"]):
                problems.append("documents.id_example does not match id_pattern")
        except re.error as e:
            problems.append(f"documents.id_pattern is not a valid regex: {e}")
    if docs["source_url"] and "{file_id}" not in docs["source_url"]:
        problems.append("documents.source_url must contain {file_id}")

    for field in data["metadata"]["fields"]:
        if not (isinstance(field, dict) and isinstance(field.get("name"), str) and isinstance(field.get("description"), str)):
            problems.append("metadata.fields entries need name and description")
        elif not _IDENTIFIER.match(field["name"]) or field["name"] in RESERVED_COLUMNS:
            problems.append(f"metadata field {field['name']!r}: use lowercase letters, digits and _, "
                            f"and none of: {', '.join(sorted(RESERVED_COLUMNS))}")
    if problems:
        raise CorpusError(f"{name}: " + "; ".join(problems))
