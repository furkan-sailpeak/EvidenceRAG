# Run `make` to list every command.
#
# First time, in this order:
#   make setup        install everything, create .env (add your API key there)
#   make configure    describe your collection (fills in corpus.toml)
#   ...put your PDFs in data/pdfs/
#   make db           start the database
#   make ocr          read the files (text, OCR for scans, page metadata)
#   make ingest       load them into the database
#   make embed        prepare meaning-based search
#   make dev          open the app at http://localhost:5173
# `make status` tells you where you are and what to run next.

VENV    := .venv
PY      := $(abspath $(VENV))/bin/python
BIN     := $(abspath $(VENV))/bin
RUN     := cd backend && $(PY)

.DEFAULT_GOAL := help
.PHONY: help setup configure models check-corpus db ocr metadata ingest embed prepare status backup \
        dev build up down logs test check hooks eval-app eval install-google-ocr

help:  ## List the commands
	@awk 'BEGIN {FS = ":.*## "} /^## / {printf "\n\033[1m%s\033[0m\n", substr($$0, 4)} \
	     /^[a-z-]+:.*## / {printf "  make %-19s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## Setup
setup:  ## Install everything (Python + frontend) and create .env
	@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is needed (python3 --version)")'
	python3 -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements-dev.txt
	cd frontend && npm install --no-audit --no-fund
	@test -f .env || { cp .env.example .env; echo "Created .env: add your API key (block 1)."; }
	@command -v tesseract >/dev/null || echo "Note: tesseract is not installed. Scanned PDFs need it: brew install tesseract (or another OCR_ENGINE in .env)."
	@command -v docker >/dev/null || echo "Note: Docker is not installed. make db / make up need it (or set DATABASE_URL in .env)."
	@echo "Done. Next: make configure"

configure:  ## Describe your collection in a step-by-step form (fills in corpus.toml)
	$(BIN)/streamlit run setup/corpus_wizard.py

models:  ## List the models your provider (LLM_PROVIDER in .env) offers right now
	@$(RUN) -c "from app import llm; print('\\n'.join(sorted(m.id for m in llm.client.models.list().data)))"

check-corpus:  ## Check corpus.toml and list anything left to fill in
	@$(RUN) -c "from app import corpus; c = corpus.load(); print('corpus.toml is complete:', c['app']['name'])"

## Data (in this order)
db:  ## Start the local database (Docker, keeps running in the background)
	docker compose up -d --wait db

ocr:  ## Read data/pdfs: text, OCR for scans, page metadata. Resumes; FORCE=1 redoes all (old results backed up); LIMIT=5 to try
	$(RUN) -m ingest.ocr $(if $(FORCE),--force) $(if $(LIMIT),--limit $(LIMIT))

metadata:  ## Only fill in missing page metadata (e.g. after adding fields to corpus.toml)
	$(RUN) -m ingest.ocr --only-metadata

ingest:  ## Load the OCR results into the database (rebuilds the pages table)
	$(RUN) -m ingest.load

embed:  ## Prepare meaning-based search (resumable; redoes all if the embedding model changed)
	$(RUN) -m ingest.embed

prepare: ocr ingest embed  ## ocr + ingest + embed in one go

status:  ## Show how far along everything is, and what to run next
	@$(RUN) -m ingest.status

backup:  ## Save corpus.toml, the OCR results and a database dump to data/backups/
	@out=data/backups/backup-$$(date +%Y%m%d-%H%M%S); mkdir -p $$out; \
	cp corpus.toml $$out/; \
	if [ -d data/ocr ]; then tar -czf $$out/ocr.tar.gz -C data ocr; fi; \
	if docker compose ps --status running -q db 2>/dev/null | grep -q .; then \
	  docker compose exec -T db pg_dump -U rag rag | gzip > $$out/database.sql.gz; \
	else echo "(local database not running: database dump skipped)"; fi; \
	echo "Backup written to $$out"

## Run the app
dev:  ## Run locally with live reload: http://localhost:5173 (Ctrl+C stops)
	@trap 'kill 0' INT TERM; \
	(cd backend && $(PY) -m app.main) & \
	(cd frontend && npm run dev -- --open) & \
	wait

build:  ## Build the frontend (the backend then serves it on :5001 too)
	cd frontend && npm run build

up:  ## Run the production version in Docker: http://localhost:8080
	docker compose --profile app up -d --build --wait
	@echo "Running at http://localhost:8080  (make logs to watch, make down to stop)"

down:  ## Stop the Docker containers (your data is kept)
	docker compose --profile app down

logs:  ## Follow the app logs (Docker)
	docker compose --profile app logs -f app

## Quality
test:  ## Run the tests (offline: no keys or database needed)
	$(BIN)/pytest

check:  ## Lint + tests + frontend lint (what CI would run)
	$(BIN)/pre-commit run --all-files
	$(BIN)/pytest
	cd frontend && npx eslint .

hooks:  ## Run the lint checks automatically before every git commit
	$(BIN)/pre-commit install

eval-app:  ## Edit evaluation question sets (eval/datasets/)
	$(BIN)/streamlit run eval/dataset_editor.py

eval:  ## Score the app on an evaluation set in LangSmith (ARGS="--limit 5")
	$(PY) eval/langsmith_eval.py run $(ARGS)

## Optional extras
install-google-ocr:  ## Google Cloud Vision, for OCR_ENGINE=google
	$(BIN)/pip install -q google-cloud-vision
