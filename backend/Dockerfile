# syntax=docker/dockerfile:1

# One image for api, worker and migrate. Both the API (query embeddings) and the worker
# (Docling extraction + chunk embeddings) need PyTorch, so a single image keeps the stack
# simple; Compose only changes the command.
#
# Stages: builder (uv + virtualenv) -> models (downloaded at build time) -> runtime.
# The dependency layer only changes when uv.lock changes; the models layer only when the
# model list below changes; the code layer is the last and smallest.

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

# --- Models, downloaded at build time ------------------------------------------------------
# At runtime HF_HUB_OFFLINE=1: a missing model fails loudly instead of being downloaded.
FROM builder AS models

# Docling: OCR is disabled in the pipeline (scans are out of scope), so only the layout
# and table-structure models are needed.
RUN /app/.venv/bin/docling-tools models download layout tableformer -o /opt/models/docling

# Embeddings: BAAI/bge-base-en-v1.5 (768 dims) into a Hugging Face cache, the layout
# sentence-transformers reads. Only the safetensors weights: the repo also ships the same
# weights as .bin and ONNX, which would triple the size.
ENV HF_HOME=/opt/models/hf
RUN /app/.venv/bin/python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('BAAI/bge-base-en-v1.5', \
    ignore_patterns=['*.bin', 'onnx/*', 'openvino/*', '*.h5', '*.ot', '*.msgpack'])"

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
COPY --from=models /opt/models /opt/models
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app app ./app

USER app

EXPOSE 8000

# Health of the API. The worker and migrate override it in Compose.
# start-period: the embedding model takes a few seconds to load.
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
