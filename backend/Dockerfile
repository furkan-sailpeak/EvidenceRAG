# The production image: built frontend + backend + corpus.toml.
# Build from the repo root (docker compose does this): make up
# The layout inside mirrors the repo (/srv/backend, /srv/corpus.toml, ...).

# --- 1. Frontend -------------------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Backend ----------------------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /srv/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Bake the default local embedding model into the image (no download at start).
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('${EMBED_MODEL}', cache_dir='/srv/data/models')"

COPY backend/app ./app
COPY backend/prompts ./prompts
COPY backend/gunicorn.conf.py ./
COPY corpus.toml /srv/corpus.toml
COPY --from=frontend /build/dist /srv/frontend/dist

EXPOSE 8080
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app.main:app"]
