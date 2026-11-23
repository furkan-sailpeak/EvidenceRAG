"""Setup wizard: fill in corpus.toml through a form instead of a text editor.

    make configure          (opens in the browser)

Loads the current corpus.toml (or the example), walks through every section
with explanations, checks the result with the same validation the app runs at
startup, and saves it. The previous file is kept as corpus.toml.bak-<time>.
"""
import json
import re
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import corpus as corpus_check  # noqa: E402  (validation only; no models or database)

CORPUS = ROOT / "corpus.toml"
EXAMPLE = ROOT / "corpus.example.toml"

st.set_page_config(page_title="Corpus setup", page_icon=":material/tune:", layout="wide")
ss = st.session_state


# --- Loading and saving -------------------------------------------------------------
def read(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def blank():
    """The template with every TODO cleared, so the form starts empty."""
    def clear(v):
        if isinstance(v, str):
            return "" if "TODO" in v else v
        if isinstance(v, list):
            return [clear(x) for x in v if not (isinstance(x, str) and "TODO" in x)]
        if isinstance(v, dict):
            return {k: clear(x) for k, x in v.items()}
        return v
    return clear(read(CORPUS)) if CORPUS.exists() else clear(read(EXAMPLE))


def start(data):
    ss.data = data
    ss.version = ss.get("version", 0) + 1   # fresh widget keys


def q(v):
    """A TOML value (JSON strings/lists are valid TOML basic strings/arrays)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {q(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + "".join(f"    {q(x)},\n" for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


SECTION_NOTES = {
    "app": "What users see in the web app",
    "collection": "How the prompts describe the collection",
    "scope": "What the assistant answers",
    "examples": "Examples that teach the router to pick search keywords",
    "background": "Optional: one-line background on well-known entities",
    "documents": "Document IDs and source links",
    "metadata": "Page metadata extracted by a vision model during `make ocr`",
}


def to_toml(d):
    out = ["# corpus.toml: everything about YOUR document collection.",
           "# Written by the setup wizard (make configure); safe to edit by hand.", ""]
    for section, note in SECTION_NOTES.items():
        out.append(f"# --- {note} ---")
        out.append(f"[{section}]")
        for key, value in d[section].items():
            if key != "columns":
                out.append(f"{key} = {q(value)}")
        out.append("")
    return "\n".join(out)


def problems(d):
    try:
        corpus_check.validate(json.loads(json.dumps(d)))
        return []
    except corpus_check.CorpusError as e:
        return [p.strip() for p in str(e).split(":", 1)[1].split(";")]


def slug(text):
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    words = [w for w in words if w not in ("the", "page", "a", "an", "contains", "is", "has")]
    name = "_".join(words[:3]) or "field"
    return ("has_" + name if not name.startswith(("has_", "is_")) else name)[:40]


if "data" not in ss:
    start(read(CORPUS) if CORPUS.exists() else blank())
d, v = ss.data, ss.version

# --- Header ---------------------------------------------------------------------------
st.title(":material/tune: Set up your collection")
st.caption(f"Fills in `{CORPUS}`. Every field explains what it does; examples come from a fictional company handbook.")
with st.container(horizontal=True):
    if st.button("Start from the example", icon=":material/menu_book:"):
        start(read(EXAMPLE))
        st.rerun()
    if st.button("Start blank", icon=":material/note_add:"):
        start(blank())
        st.rerun()

tabs = st.tabs(["1 · App", "2 · Collection", "3 · Scope", "4 · Search examples", "5 · Background",
                "6 · Documents", "7 · Page metadata", "8 · Review & save"])

# --- 1. App ---------------------------------------------------------------------------------
with tabs[0]:
    st.subheader("What users see")
    a = d["app"]
    a["name"] = st.text_input("Assistant name", a["name"], key=f"name{v}", placeholder="Handbook Assistant",
                              help="Page title, welcome heading, and how the assistant introduces itself.")
    a["tagline"] = st.text_input("Tagline", a["tagline"], key=f"tag{v}", placeholder="Search the Acme employee handbook")
    a["welcome"] = st.text_area("Welcome text", a["welcome"], key=f"wel{v}", height=80,
                                placeholder="Ask about Acme's HR policies... Every answer cites its pages.")
    a["input_placeholder"] = st.text_input("Text in the empty question box", a["input_placeholder"], key=f"ph{v}")
    qs = st.data_editor(pd.DataFrame({"Example question": a["example_questions"] or [""]}), num_rows="dynamic",
                        key=f"exq{v}", hide_index=True, width="stretch",
                        alt="Example questions shown on the welcome screen")
    a["example_questions"] = [x.strip() for x in qs["Example question"].fillna("") if x.strip()]
    st.caption("Clickable on the welcome screen. Use 3–4 real questions your documents answer.")
    a["footer"] = st.text_input("Footer (optional)", a["footer"], key=f"foot{v}", placeholder="© 2026 Acme · Internal use only")

# --- 2. Collection -------------------------------------------------------------------------
with tabs[1]:
    st.subheader("How the assistant talks about your documents")
    c = d["collection"]
    c["name"] = st.text_input("The collection, completing \"questions about ...\"", c["name"], key=f"cn{v}",
                              placeholder="the Acme employee handbook and HR policies")
    c["document_noun"] = st.text_input("What one file is called", c["document_noun"], key=f"dn{v}",
                                       placeholder="document, policy, report, contract...")
    c["vocabulary"] = st.text_input("Kinds of sources in the collection", c["vocabulary"], key=f"voc{v}",
                                    placeholder="HR policies, benefit guides, forms and internal memos",
                                    help="Helps the search try other wordings when a first answer is weak.")
    c["persona"] = st.text_input("The voice answers are written in (keep \"a\"/\"an\")", c["persona"], key=f"per{v}",
                                 placeholder="an expert HR policy assistant")

# --- 3. Scope -------------------------------------------------------------------------------
with tabs[2]:
    st.subheader("What the assistant answers")
    d["scope"]["in_scope"] = st.text_area(
        "Topics that are in scope", d["scope"]["in_scope"], key=f"scope{v}", height=80,
        placeholder="Acme's HR policies, benefits, leave, expenses, remote work and workplace rules")
    st.info("Questions on any other subject get a polite refusal. Be generous: list the broad themes, not every detail.",
            icon=":material/info:")

# --- 4. Search examples -------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Teach the search which words matter")
    st.write("For a few typical questions, write the 1–3 words that best find the answer: names, places, specific "
             "terms. Leave out filler like *role*, *information*, *policy about*.")
    ex = d["examples"]
    rows = [{"Question": k["question"], "Search words (comma separated)": ", ".join(k["terms"])} for k in ex["keywords"]]
    table = st.data_editor(pd.DataFrame(rows or [{"Question": "", "Search words (comma separated)": ""}]),
                           num_rows="dynamic", key=f"kw{v}", hide_index=True, width="stretch",
                           alt="Example questions with their search words")
    ex["keywords"] = [{"question": r["Question"].strip(),
                       "terms": [t.strip() for t in str(r["Search words (comma separated)"]).split(",") if t.strip()]}
                      for r in table.fillna("").to_dict("records") if str(r["Question"]).strip()]
    st.divider()
    st.write("**A follow-up question** that only makes sense after the previous one, and how it reads on its own.")
    f = ex["followup"]
    cols = st.columns(3)
    f["previous"] = cols[0].text_input("Previous topic", f.get("previous", ""), key=f"fp{v}", placeholder="parental leave")
    f["question"] = cols[1].text_input("Follow-up question", f.get("question", ""), key=f"fq{v}", placeholder="how long does it last?")
    f["rewritten"] = cols[2].text_input("Standalone version", f.get("rewritten", ""), key=f"fr{v}",
                                        placeholder="how long does parental leave last?")

# --- 5. Background -------------------------------------------------------------------------------
with tabs[4]:
    st.subheader("Background on well-known names (optional)")
    b = d["background"]
    b["enabled"] = st.toggle("Let the assistant add one identifying sentence from general knowledge", b["enabled"],
                             key=f"bg{v}", help="Useful for public topics (history, science). Keep it off for "
                                                "private or internal documents.")
    if b["enabled"]:
        b["context"] = st.text_input("The topic", b["context"], key=f"bgc{v}", placeholder="the history of the Apollo space program")
        bt = st.data_editor(pd.DataFrame({"Example sentence": b["examples"] or [""]}), num_rows="dynamic",
                            key=f"bge{v}", hide_index=True, width="stretch", alt="Example identification sentences")
        b["examples"] = [x.strip() for x in bt["Example sentence"].fillna("") if x.strip()]
        st.caption('2–3 sentences like "Neil Armstrong was the first person to walk on the Moon, in 1969."')

# --- 6. Documents ------------------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Document IDs and source links")
    doc = d["documents"]
    use_ids = st.toggle("My file names are document IDs people will type (e.g. POL-0042)", bool(doc["id_pattern"]), key=f"ids{v}")
    if use_ids:
        doc["id_example"] = st.text_input("An example ID (= a file name without .pdf)", doc["id_example"], key=f"ide{v}",
                                          placeholder="POL-0042")
        guess = ""
        if doc["id_example"]:
            guess = r"\b(" + re.sub(r"\d+", lambda m: rf"\d{{{len(m.group())}}}", re.escape(doc["id_example"])) + r")\b"
        doc["id_pattern"] = st.text_input("ID pattern (regular expression with one group)", doc["id_pattern"] or guess,
                                          key=f"idp{v}", help="Filled in from your example; only change it if your IDs vary.")
    else:
        doc["id_pattern"], doc["id_example"] = "", ""
    public = st.toggle("The documents have public links (otherwise the app serves its own copy)", bool(doc["source_url"]),
                       key=f"pub{v}")
    doc["source_url"] = st.text_input("Link pattern, with {file_id} where the file name goes", doc["source_url"],
                                      key=f"url{v}", placeholder="https://files.example.com/{file_id}.pdf") if public else ""

# --- 7. Page metadata -----------------------------------------------------------------------------------
with tabs[6]:
    st.subheader("Page properties to extract (optional)")
    st.write("A vision model looks at every page during `make ocr` and records these, so users can ask "
             "*\"how many pages are signed?\"* or *\"show me the forms\"*. Leave both empty to skip it (faster, no vision model needed).")
    m = d["metadata"]
    ft = st.data_editor(pd.DataFrame([{"Yes/no question about a page": f["description"]} for f in m["fields"]]
                                     or [{"Yes/no question about a page": ""}]),
                        num_rows="dynamic", key=f"mf{v}", hide_index=True, width="stretch",
                        alt="Yes/no properties extracted per page")
    descs = [x.strip() for x in ft["Yes/no question about a page"].fillna("") if x.strip()]
    names, fields = set(), []
    for desc in descs:
        old = next((f["name"] for f in m["fields"] if f["description"] == desc), None)
        name = old or slug(desc)
        while name in names:
            name += "_2"
        names.add(name)
        fields.append({"name": name, "description": desc})
    m["fields"] = fields
    st.caption("Write them as statements: \"the page contains a table\", \"the page is signed\". "
               + (f"Stored as: {', '.join(f['name'] for f in fields)}" if fields else ""))
    types = st.text_input("Document types (comma separated; each page gets exactly one)",
                          ", ".join(m["document_types"]), key=f"dt{v}", placeholder="letter, memo, form, report")
    m["document_types"] = [t.strip() for t in types.split(",") if t.strip()]

# --- 8. Review & save --------------------------------------------------------------------------------------
with tabs[7]:
    issues = problems(d)
    if issues:
        st.warning("Not ready yet:\n\n" + "\n".join(f"- {p}" for p in issues), icon=":material/checklist:")
    else:
        st.success("Everything is filled in and valid.", icon=":material/check_circle:")
    text = to_toml(d)
    with st.expander("Preview corpus.toml", icon=":material/description:"):
        st.code(text, language="toml")
    if st.button("Save corpus.toml", type="primary", icon=":material/save:", disabled=bool(issues)):
        backup = None
        if CORPUS.exists():
            backup = CORPUS.with_name(f"corpus.toml.bak-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy(CORPUS, backup)
        CORPUS.write_text(text)
        st.success(f"Saved {CORPUS}" + (f" (previous version kept as {backup.name})" if backup else ""))
        st.markdown("**Next:** put your PDFs in `data/pdfs/`, then run `make ocr`, `make ingest`, `make embed`.")
