# Decisions

## 2026-11-03 · Provider spikes

Script: `backend/scripts/spike_providers.py` (uv group `spike`). Versions: litellm 1.106.6,
google-genai 2.34.0. Run from `backend/`:
`uv run --group spike python scripts/spike_providers.py <command>`.

### Environment

- `cryptography` 50.x ships no wheel for macOS 12 x86_64 and tries to build with Rust (fails).
  `cryptography<49` is pinned in the `spike` group (48.0.1 has a wheel). Not relevant in Docker (Linux).

### Gemini LLM with schema-constrained JSON output (LiteLLM)

- Flash models listed in AI Studio: `gemini-3.8-flash` (the newest), `gemini-3.5-flash`,
  `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, plus previews.
- `gemini-2.5-flash`: **404, no longer available to new users**.
- `gemini-3.8-flash`: **503 UNAVAILABLE ("high demand")** in 3 of 3 attempts (plus a connection timeout).
- `gemini-3.5-flash`: OK with `response_format=<Pydantic model>`. Valid JSON per the schema,
  the verbatim quote appears in the context. Latency ≈ 4.4 s; 97 input / 296 output tokens.
  A later attempt: 503.
- `gemini-3.5-flash-lite`: OK, valid JSON, correct quote. Latency ≈ 19.6 s (at a time of
  high load); 97 / 92 tokens.
- **Decision:** `LLM_MODEL=gemini/gemini-3.5-flash` as the default for real mode;
  `gemini-3.5-flash-lite` as the alternative. Model ids only via environment variable.
- **Implication:** Gemini 503s are frequent. Treat 503 (like 429) as **transient** with backoff.

### Gemini embeddings

- `gemini-embedding-001` with `output_dimensionality=768`:
  - Batch of 3 texts → 3 vectors of 768 (≈ 0.8–1.6 s per batch).
  - Norm before normalizing ≈ 0.59 (**not normalized**); after L2 = 1.0000.
  - `RETRIEVAL_DOCUMENT` vs. `RETRIEVAL_QUERY` for the same text: cosine 0.90, so the task type
    does change the vector.
- LiteLLM (`gemini/gemini-embedding-001`, `dimensions=768`, `task_type=...`): honors the dimension
  and the task type; cosine against the SDK vector = 1.0000 (identical).
- `gemini-embedding-2` (tried for comparison): with the SDK, a batch of 3 texts returns **a single
  vector** (it seems to aggregate the `contents`) and the task type does not change the vector
  (cosine 1.0). With LiteLLM it returns 3 vectors, but different from the SDK's (cosine 0.76).
  Unclear behavior: discarded for the MVP.
- **Decision:** `EMBEDDING_DIM=768` confirmed, model `gemini-embedding-001`, version stored as
  `gemini-embedding-001@768`. Always L2-normalize before storing. LiteLLM works for
  embeddings (the `google-genai` SDK is not needed in the app); a single library is used.

### Groq

- Dropped from scope: Gemini is the only optional real provider.
