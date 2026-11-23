import pytest

from app import config, generation, prompts

REFUSAL = config.REFUSAL_LINE


# --- History ----------------------------------------------------------------------
def test_sanitize_history_bounds_and_filters():
    raw = [{"role": "system", "content": "x"}, {"role": "user", "content": "a" * 10_000}, "junk", {"role": "assistant", "content": 5}]
    assert generation.sanitize_history(raw) == [{"role": "user", "content": "a" * config.MAX_HISTORY_CHARS}]


def test_summarize_last_answer_uses_head_and_tail():
    long = "HEAD" + "x" * 2000 + "TAIL"
    out = generation.summarize_last_answer([{"role": "assistant", "content": long}], char_cap=100)
    assert out.startswith("HEAD") and out.endswith("TAIL") and " ... " in out


# --- Router -----------------------------------------------------------------------
def test_route_query_falls_back_without_llm():
    # Offline, judge_client is None: the router must degrade, not raise.
    route = generation.route_query("Who was Tom Becker?", [])
    assert route.query_type == "research" and route.needs_retrieval
    assert route.rewritten_query == "Who was Tom Becker?"
    assert route.search_terms == ["Tom", "Becker"]  # content words, not every token


def test_route_query_parses_reply_and_sends_history(llm_replies):
    create = llm_replies("judge_client", {
        "rewritten_query": "Did Maria Lopez's wife testify?", "keywords": ["Maria Lopez"],
        "type": "simple", "needs_retrieval": True,
    })
    hist = [{"role": "user", "content": "Who was Lopez?"}, {"role": "assistant", "content": "A Marine [1]."}]
    route = generation.route_query("Did his wife testify?", hist)
    assert route.query_type == "simple"
    assert route.search_terms == ["Maria Lopez"]
    assert route.rewritten_query == "Did Maria Lopez's wife testify?"
    prompt = create.call_args.kwargs["messages"][0]["content"]
    assert "Did his wife testify?" in prompt and "Who was Lopez?" in prompt


def test_route_query_unknown_type_becomes_research(llm_replies):
    llm_replies("judge_client", {"type": "chitchat", "keywords": ["x"]})
    assert generation.route_query("q", []).query_type == "research"


def test_route_query_missing_keywords_uses_query_words(llm_replies):
    llm_replies("judge_client", {"type": "simple"})
    route = generation.route_query("Tom Becker club", [])
    assert route.search_terms == ["Tom", "Becker", "club"] and route.rewritten_query == "Tom Becker club"


def test_route_query_caps_search_terms(llm_replies):
    llm_replies("judge_client", {"type": "research", "keywords": [f"Name{i}" for i in range(300)]})
    assert len(generation.route_query("q", []).search_terms) == config.MAX_SEARCH_TERMS


def test_route_query_failure_fallback_is_bounded():
    # Offline the router raises; a 2000-char query must not become 400 terms.
    route = generation.route_query(" ".join(f"Word{i}" for i in range(400)), [])
    assert len(route.search_terms) <= config.MAX_SEARCH_TERMS


def test_route_query_parses_metadata_filter(llm_replies):
    llm_replies("judge_client", {"type": "metadata", "metadata_filter": {"column": "has_redactions", "value": True}})
    route = generation.route_query("Which pages are redacted?", [])
    assert route.metadata_filter.column == "has_redactions" and route.metadata_filter.value is True


# --- Sources ----------------------------------------------------------------------
def test_build_context_tags_sources_and_neutralises_ocr_brackets():
    ctx = generation.build_context([("FORM [7] FIELD\n\nmore text", "a.pdf", 3), ("x" * 50, "b.pdf", 1)], cap=20)
    assert '<source id="1" file="a.pdf" page="3">' in ctx and '</source id="1">' in ctx
    assert '<source id="2" file="b.pdf" page="1">' in ctx
    assert "[7]" not in ctx and "FORM (7) FIELD" in ctx
    assert "x" * 21 not in ctx  # cap applied per source


def test_build_context_handles_pages_without_text():
    # Some documents have pages with NULL content (no OCR text).
    ctx = generation.build_context([(None, "a.pdf", 1), ("text", "a.pdf", 2)])
    assert '<source id="1" file="a.pdf" page="1">' in ctx and "text" in ctx


# --- Background -------------------------------------------------------------------
def test_entity_brief_caches_none(llm_replies):
    generation._entity_brief_cache.clear()
    create = llm_replies("client", "NONE")
    assert generation.entity_brief("New York Station") is None
    assert generation.entity_brief("New York Station") is None
    create.assert_called_once()


def test_entity_brief_skips_non_proper_nouns(llm_replies):
    create = llm_replies("client")
    assert generation.entity_brief("surveillance") is None
    create.assert_not_called()


@pytest.fixture
def background_on(mocker):
    mocker.patch.dict(config.CORPUS["background"], {"enabled": True})


def test_background_block_is_off_unless_enabled(mocker):
    brief = mocker.patch.object(generation, "entity_brief")
    assert generation.build_background_block(["Neil Armstrong"]) == ""
    brief.assert_not_called()


def test_background_block_caps_llm_fanout(llm_replies, background_on):
    # Regression: one request made one paid call per term, 400 in the audit.
    generation._entity_brief_cache.clear()
    create = llm_replies("client", *["X is a person."] * 400)
    generation.build_background_block([f"Name{i}" for i in range(400)])
    assert create.call_count == config.MAX_ENTITY_BRIEFS


def test_background_block_keeps_first_spelling_of_duplicates(mocker, background_on):
    brief = mocker.patch.object(generation, "entity_brief", side_effect=lambda t: f"{t} was a person.")
    block = generation.build_background_block(["Tom Becker", "tom becker", ""])
    brief.assert_called_once_with("Tom Becker")
    assert "- Tom Becker was a person." in block


# --- Answer prompts ---------------------------------------------------------------
@pytest.mark.parametrize("query_type", ["simple", "research"])
def test_user_prompt_is_fully_rendered_with_reminder_last(query_type):
    user = generation.build_rag_user_prompt("Who was Tom Becker?", "<source id=\"1\">text</source id=\"1\">", "prior", query_type, "BG\n\n")
    assert "{{" not in user and "}}" not in user
    # The format reminder must be the tail of the prompt, after the sources.
    reminder = prompts.load(f"rag-format-{query_type}.j2")
    assert user.rstrip().endswith(reminder.rstrip())
    assert user.index("RETRIEVED DOCUMENTS") < user.index("FORMAT REMINDER")


# --- Citation retry ---------------------------------------------------------------
@pytest.mark.parametrize("text, picked", [
    ("Cited claim [1].", [("c", "a.pdf", 1)]),
    (REFUSAL, [("c", "a.pdf", 1)]),
    ("Uncited but nothing retrieved.", []),
])
def test_retry_if_uncited_skips(llm_replies, text, picked):
    create = llm_replies("client")
    assert generation.retry_if_uncited(text, "q", picked, "sys") == text
    create.assert_not_called()


def test_retry_if_uncited_takes_cited_retry(llm_replies):
    create = llm_replies("client", "Becker owned the Harbor Cafe [1].")
    out = generation.retry_if_uncited("Becker owned a club.", "q", [("Harbor Cafe", "a.pdf", 1)], "sys")
    assert out == "Becker owned the Harbor Cafe [1]."
    assert "Harbor Cafe" in create.call_args.kwargs["messages"][1]["content"]


def test_retry_if_uncited_keeps_original_when_retry_still_uncited(llm_replies):
    llm_replies("client", "Still no citations.")
    assert generation.retry_if_uncited("Original.", "q", [("c", "a.pdf", 1)], "sys") == "Original."


def test_generate_answer_cleans_and_retries(llm_replies):
    llm_replies("client", "Step 1: plan\nBecker owned a club.", "Becker owned a club 【1】.")
    out = generation.generate_answer("q", [("club", "a.pdf", 1)], "sys", "", "simple", "")
    assert out == "Becker owned a club [1]."
