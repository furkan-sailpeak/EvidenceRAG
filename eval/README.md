# eval/

Measure answer quality, and check that a change did not make it worse.

1. `make eval-app`: create a question set (`datasets/dataset_<project>_<date>.json`). For each question,
   write the answer you expect and the pages it should cite; tick *validated* once checked.
2. Add `LANGSMITH_API_KEY` to `.env` (free account at smith.langchain.com).
3. `make eval` (or `make eval ARGS="--validated-only --limit 10"`): runs every question through the
   pipeline and scores it in LangSmith: correctness (a judge model compares with your answer),
   citations, whether the expected pages were found and cited, and refusals on off-topic questions.

Run it before and after a change; experiments are named after the git commit, so they line up in
LangSmith's comparison view. Scores vary a few points between identical runs, so only trust
differences larger than that.
