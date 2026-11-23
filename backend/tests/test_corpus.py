"""corpus.toml: the example loads, the blank template is rejected with a list
of TODOs, bad values fail loudly, and every prompt is filled from the corpus."""
import copy
import os
import re

import pytest
from jinja2 import meta

from app import config, corpus, prompts

ROOT = corpus.REPO_ROOT
PROMPT_FILES = sorted(f for f in os.listdir(prompts.PROMPTS_DIR) if f.endswith(".j2"))


def _example():
    return copy.deepcopy(corpus.load(os.path.join(ROOT, "corpus.example.toml")))


def _render_all(c):
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(prompts._env.globals, "corpus", c)
        for name in PROMPT_FILES:
            variables = meta.find_undeclared_variables(prompts._env.parse(prompts.source(name)))
            out[name] = prompts.render(name, **{v: "x" for v in variables - set(prompts._env.globals)})
    return out


def test_example_corpus_loads():
    c = _example()
    assert c["metadata"]["columns"] == ["has_tables", "has_signature", "document_type"]


def test_blank_template_lists_every_todo():
    with pytest.raises(corpus.CorpusError, match="replace the TODO values in app.name, app.tagline"):
        corpus.load(os.path.join(ROOT, "corpus.toml"))


def test_template_and_example_have_the_same_keys():
    import tomllib
    with open(os.path.join(ROOT, "corpus.toml"), "rb") as f:
        template = tomllib.load(f)
    example = _example()
    for section, keys in template.items():
        assert set(keys) == set(example[section]) - {"columns"}, section


def test_prompts_have_no_collection_specific_text():
    """Rendered with the example corpus, the only domain words come from it."""
    for name, text in _render_all(_example()).items():
        assert not re.search(r"jfk|kennedy|oswald|archiv|declassif|historian|\bcia\b|\bfbi\b", text, re.I), name


def test_corpus_values_reach_the_prompts():
    rendered = _render_all(_example())
    assert "the Acme employee handbook" in rendered["router.j2"]
    assert '"vacation days", "new employees"' in rendered["router.j2"]
    assert '"has_tables", "has_signature", "document_type"' in rendered["router.j2"]
    assert "POL-0042" in rendered["conversational.j2"]
    assert "an expert HR policy assistant" in rendered["rag-simple.j2"]


def test_router_drops_metadata_when_no_fields():
    c = _example()
    c["metadata"].update(fields=[], document_types=[], columns=[])
    router = _render_all(c)["router.j2"]
    assert '"metadata"' not in router and "metadata_filter" not in router


def test_no_document_id_example_without_a_pattern():
    c = _example()
    c["documents"].update(id_pattern="", id_example="")
    assert "by its ID" not in _render_all(c)["conversational.j2"]


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["app"].pop("name"), "missing app.name"),
    (lambda c: c.pop("scope"), "missing section [scope]"),
    (lambda c: c["collection"].update(name=""), "collection.name is empty"),
    (lambda c: c["app"].update(tagline="TODO later"), "replace the TODO values in app.tagline"),
    (lambda c: c["examples"].update(keywords=[{"question": "q"}]), "need question"),
    (lambda c: c["examples"]["followup"].pop("rewritten"), "followup needs previous, question and rewritten"),
    (lambda c: c["background"].update(enabled=True), "background.enabled = true needs"),
    (lambda c: c["documents"].update(id_pattern=r"POL-\d+"), "exactly one (group)"),
    (lambda c: c["documents"].update(id_example="X-1"), "does not match id_pattern"),
    (lambda c: c["documents"].update(source_url="https://x/{id}.pdf"), "must contain {file_id}"),
    (lambda c: c["metadata"].update(fields=[{"name": "Bad Name", "description": "d"}]), "use lowercase"),
    (lambda c: c["metadata"].update(fields=[{"name": "content", "description": "d"}]), "use lowercase"),
])
def test_invalid_corpus_fails_loudly(mutate, message):
    bad = _example()
    bad["metadata"].pop("columns")
    mutate(bad)
    with pytest.raises(corpus.CorpusError, match=re.escape(message)):
        corpus.validate(bad)


def test_backend_settings_come_from_the_corpus():
    assert config.PAGES_TABLE == "pages"
    assert config.DOC_ID.search("open POL-0042 please").group(1) == "POL-0042"
    assert config.METADATA_COLUMNS == {"has_tables", "has_signature", "document_type"}
