# syntax=docker/dockerfile:1

# One image for api, worker and migrate. Both the API (query embeddings) and the worker
# (Docling extraction + chunk embeddings) need PyTorch, so a single image keeps the stack
# simple; Compose only changes the command.
#
# Stages: builder (uv + virtualenv), embedding-model and docling-models (downloaded at
# build time), runtime. The embedding model does NOT depend on uv.lock: adding a Python
# dependency must not download ~440 MB of weights again. Docling's models are optional
# (pdfplumber is the default extractor): `--build-arg WITH_DOCLING_MODELS=true`.

# --- Builder: virtualenv with uv ----------------------------------------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Manifests only: the dependency layer stays cached as long as the lock does not change.
COPY pyproject.toml uv.lock .python-version ./

# Main dependencies plus the `ml` group (Docling, CPU-only PyTorch, sentence-transformers).
# No dev tools in the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-default-groups --group ml --no-install-project

# --- Embedding model, independent of the app's dependencies -------------------------------
# BAAI/bge-base-en-v1.5 (768 dims) into a Hugging Face cache, the layout sentence-transformers
# reads. Only the safetensors weights: the repo also ships the same weights as .bin and
# ONNX, which would triple the size. huggingface_hub is pinned to the version in uv.lock.
FROM python:3.12-slim AS embedding-model
RUN --mount=type=cache,target=/root/.cache/pip pip install "huggingface_hub==1.33.0"
ENV HF_HOME=/opt/models/hf
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('BAAI/bge-base-en-v1.5', \
    ignore_patterns=['*.bin', 'onnx/*', 'openvino/*', '*.h5', '*.ot', '*.msgpack'])"
# The reranker (RERANKER=cross_encoder): cross-encoder/ms-marco-MiniLM-L-6-v2, ~90 MB of
# safetensors weights. Its own layer, so changing it does not download BGE again.
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('cross-encoder/ms-marco-MiniLM-L-6-v2', \
    ignore_patterns=['*.bin', 'onnx/*', 'openvino/*', '*.h5', '*.ot', '*.msgpack'])"

# --- Docling models (optional) ---------------------------------------------------------------
# Only needed with TEXT_EXTRACTOR=docling. OCR is disabled in the pipeline, so only the
# layout and table-structure models. Without them the directory is empty and Docling fails
# loudly at runtime (HF_HUB_OFFLINE=1) instead of downloading.
FROM builder AS docling-models
ARG WITH_DOCLING_MODELS=false
RUN mkdir -p /opt/models/docling \
    && if [ "$WITH_DOCLING_MODELS" = "true" ]; then \
         /app/.venv/bin/docling-tools models download layout tableformer -o /opt/models/docling; \
       fi

# --- Runtime: no uv, no build tools, non-root user -----------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # `python scripts/<name>.py` must be able to `import app`.
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    # Models baked into the image; nothing is downloaded at runtime.
    DOCLING_ARTIFACTS_PATH=/opt/models/docling \
    HF_HOME=/opt/models/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# uid/gid 1000: `migrate`, `api` and `worker` share volumes (uploaded files, the JWT key)
# and must see the same owner. The mount points are created here, owned by that user, so
# a new named volume mounted on them starts with that owner (Docker copies it).
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --no-create-home app \
    && mkdir -p /data/files /run/rutaviva \
    && chown app:app /data/files /run/rutaviva \
    && chmod 700 /run/rutaviva

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
# Owned by root, readable by everyone: the app can load the models but not change them.
COPY --from=embedding-model /opt/models/hf /opt/models/hf
COPY --from=docling-models /opt/models/docling /opt/models/docling
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app app ./app

USER app

EXPOSE 8000

# Health of the API. The worker and migrate override it in Compose.
# start-period: the embedding model takes a few seconds to load.
# timeout 10s: on a small machine a busy CPU (an embedding in progress) delays the answer.
HEALTHCHECK --interval=15s --timeout=10s --start-period=60s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
