# Rutaviva Contract Intelligence

A multi-tenant assistant for supplier contracts. A procurement team uploads contract PDFs; a worker processes them in the background (text, chunks with their page, embeddings); then the team asks questions in plain language and gets answers with clickable citations (document, page, and the passage highlighted on the PDF). Two teams never see each other's documents.

It runs **with no paid keys**: a keyless extractive LLM and a real embedding model that runs locally. A real LLM (Gemini through LiteLLM) can replace the keyless one with an environment variable.

**Stack:** FastAPI · Celery + Redis · PostgreSQL 16 + pgvector · React 18 + TypeScript · OpenTelemetry → Jaeger · Docker Compose · GitHub Actions.

Design decisions, the answers to the brief's open questions, known limits and how generative AI was used are in [DECISIONS.md](DECISIONS.md).

---

## Start it (one command)

Requirements: Docker with Compose v2, and about 4 GB of memory and 6 GB of disk for Docker.

```bash
docker compose up --build        # or: make up  (same thing, in the background)
```

No `.env` is needed. The first build takes several minutes: it downloads the embedding model (`BAAI/bge-base-en-v1.5`) into the image, so nothing is downloaded at run time. When `api`, `worker` and `web` are `healthy` (`docker compose ps`):

| What | Where |
|---|---|
| Web app | http://localhost:8080 |
| API docs (Swagger) | http://localhost:8000/docs |
| Traces (Jaeger) | http://localhost:16686 |

On start, the one-shot `migrate` service generates the JWT signing key into a Docker volume, applies the migrations and creates two tenants: `tenant_a` (Fleet Procurement) and `tenant_b` (Freight & Risk Procurement).

## A five-minute tour

1. **Sign in:** open http://localhost:8080 and click **Sign in as Fleet Procurement** (`tenant_a`). This is a demo shortcut, not authentication: the API signs a test JWT (valid 24 h) for the tenant you pick, with no password, and the app shows a "Demo mode" badge while it is on. It is on by default; `DEMO_LOGIN=false` in `.env` turns it off (ADR-016), and then you paste a token instead.
2. **Second tenant:** with the **+** in the top bar, sign in as **Freight & Risk Procurement** (`tenant_b`) as well: the top bar switches between them. For scripts, or with demo mode off, `make token tenant=tenant_a` still prints a token to paste under "Use a token (advanced)".
3. **Upload:** drag the PDFs of [`sample_data/pdfs/tenant_a/`](sample_data/pdfs/tenant_a). Each one goes `pending` → `processing` → `ready` (about 20 s for a 2-page contract on a 4-CPU machine). `corrupted_contract.pdf` ends `failed` with `corrupt_pdf`; uploading the same PDF twice says it is already there.
4. **Ask** (Chat), as `tenant_a`:
   - *"What is the penalty for late tire delivery?"*: the agreement and its SLA exhibit disagree. The answer cites both and its confidence is capped at medium.
   - *"Is there a minimum monthly volume commitment in the PetroRuta fuel agreement, and what happens if we buy less?"*: an exhibit of that PDF carries a prompt injection. It is excluded and the answer says so.
   - *"Is refrigeration breakdown on reefer trailers covered by any of our contracts?"*: no contract covers it, so the answer is "There is not enough information in your documents to answer this question."
   - Click a citation: the PDF opens on that page with the passage highlighted.
5. **Isolation:** switch to `tenant_b`. The documents of A are gone, and the same questions find nothing of A's. Upload `sample_data/pdfs/tenant_b/` and ask *"What is the detention rate and how much free time applies at each pickup or delivery appointment?"*.
6. **Traces:** in Jaeger, service `rutaviva-api`:
   - operation `POST /documents`: one trace crosses the API and the worker (extract, chunk, embed, store);
   - operation `POST /query/stream`: retrieval, the LLM call with its latency and tokens, and a link to the ingestion trace of each document used.
   - Every log line carries the same `trace_id` (`docker compose logs worker`).
   - How the traces are laid out, and why a question is a separate trace: [Observability](#observability).

More questions, with the expected answers and pages, are in [`sample_data/questions.yaml`](sample_data/questions.yaml).

## Observability

```
api / worker  ──OTLP──►  otel-collector  ──OTLP──►  jaeger (UI on :16686)
```

The collector receives the spans and forwards them; Jaeger (all-in-one, in-memory storage) stores and shows them.

**Services in Jaeger.** Each process exports under its own name: `rutaviva-api`, `rutaviva-worker` and `rutaviva-migrate` (the migrations at start-up). The service picker is only a search filter. A trace is not split by service: the upload trace below holds spans from the API *and* the worker, so searching `rutaviva-api` or `rutaviva-worker` finds the same trace.

**One trace per upload: HTTP request → Celery task → SQL.** `POST /documents` stores the file and enqueues the task; the Celery instrumentation puts the trace context in the message, so the worker's spans (extract, chunk, embed, store) continue the same trace. Every SQL statement, in the API and in the worker, is a child span of whatever step ran it.

**One trace per question, linked to the uploads.** `POST /query/stream` has its own trace: query embedding, hybrid retrieval, relevance judge (real LLM only), `llm.generate` and the validated answer. A question is not caused by an upload, and it can come days later, so it is not forced into the upload's trace. Instead, its `query.answer` span carries a **span link** to the ingestion trace of each document it used: in Jaeger, one click goes from an answer to how its sources were indexed.

**Attributes** (ids, counts and timings only, never document content or prompts):

| Attribute | Where |
|---|---|
| `tenant.id` | every authenticated request |
| `document.id`, `document.pages`, `chunks.count` | upload and ingestion |
| `retrieval.vec_hits`, `retrieval.fts_hits`, `retrieval.results` | retrieval |
| `gen_ai.request.model`, `llm.latency_ms`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `llm.tokens.estimated` | `llm.generate` |
| `answer.confidence`, `answer.citations`, `prompt_injection.flagged` | the answer |

**Logs.** JSON, one event per line, with the current `trace_id` and `span_id`, the same ids as in Jaeger. API errors (`application/problem+json`) and the chat's "How it was answered" panel show the `trace_id` too, so `docker compose logs api worker | grep <trace_id>` gives every log line of a request. The same lines are also in Jaeger: each one logged inside a span is added to that span as an event (open a span → "Logs"). Jaeger stores no logs of its own; stdout stays the full log.

## Keyless mode and a real LLM

| Setting | Default (no key) | Real provider |
|---|---|---|
| `LLM_PROVIDER` | `fake`: an extractive LLM that answers with the contract's own sentences, ranked with the embedding model, behind a calibrated "not enough information" rule | `litellm`, with `LLM_MODEL=gemini/gemini-3.5-flash` and `GEMINI_API_KEY` |
| `EMBEDDINGS_PROVIDER` | `local`: BGE base (768 dimensions) on the CPU, inside the image | `gemini`, together with `LLM_PROVIDER=litellm`. Start from an empty database (`docker compose down -v`): documents indexed with another model are not searched, and uploading the same PDF again returns the existing document (there is no reindex job) |
| `TEXT_EXTRACTOR` | `pdfplumber` | `docling` (build with `WITH_DOCLING_MODELS=true`; minutes per PDF on a CPU) |
| `RERANKER` | `cross_encoder`: `ms-marco-MiniLM-L-6-v2` on the CPU, inside the image, reorders the search candidates | the same (`none` keeps the fused RRF order) |

To use a real LLM, copy `.env.example` to `.env`, set those values and run `docker compose up -d`. A real provider without its key stops start-up with a clear message; it never falls back to the fake one silently. The fake LLM can also simulate failures for testing (`FAKE_LLM_MODE=invalid | invalid_once | timeout | empty`).

## Tests

```bash
# Backend: unit, contract and integration tests (integration ones use the real Postgres)
docker compose up -d db
make test                        # cd backend && uv run pytest   (needs uv)

# Frontend: lint, types, tests, build
cd frontend && npm ci && npm run lint && npm run typecheck && npm test && npm run build

# Behaviour evaluation against the running stack (sample PDFs + verification questions)
make eval
```

- **Unit:** chunking, PDF extraction, LLM output validation (JSON, schema, grounding), guardrails, ranking, follow-up resolution, configuration.
- **Integration (real PostgreSQL):** hybrid search, tenant isolation through the API and under RLS, the Celery task with failure and retry, duplicates, corrupt and empty PDFs.
- **The LLM and the embeddings are simulated in tests** (`EMBEDDINGS_PROVIDER=fake`, a deterministic hashing embedder), so tests need no model and no network.
- **Evaluation:** `make eval` uploads the sample PDFs, asks the 21 questions and checks behaviour: the outcome, the cited page, required and forbidden strings, that nothing of another tenant is retrieved, confidence caps and warnings. Last run: 16 pass, 4 soft warnings, 1 soft miss (details in ADR-014).

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs four jobs:
- **backend:** lint, migrations up, down and up again, tests against a real Postgres; it fails if the database tests are skipped or the OpenAPI document is out of date;
- **frontend:** lint, generated types, type check, tests, build;
- **e2e:** the whole stack with `docker compose up --build`, `make eval`, and a check of the traces through Jaeger's API;
- **gitleaks:** a secret scan.

## Repository layout

```
backend/        FastAPI API, Celery worker, ingestion, retrieval, generation, migrations, tests
  app/          api/ auth/ ingestion/ retrieval/ generation/ conversations/ providers/ worker/ core/
  scripts/      eval harness, token issuer, calibration, OpenAPI export, trace check
frontend/       React + TypeScript app (see frontend/README.md)
infra/          OpenTelemetry Collector and Nginx configuration
sample_data/    fictitious contract PDFs (and their generator), verification questions, calibration set
DECISIONS.md    ADRs, open questions, limits, use of generative AI
```

## What is done

- Upload per tenant returns the id and `pending` at once; type (magic bytes) and size validated; duplicates detected by SHA-256 per tenant.
- Celery task (Redis broker): extract → chunk by page, with bounding boxes → embed → store. Idempotent and retryable: a lease with attempts and fencing, `acks_late`, and a closed set of failure codes. No document is left stuck in `processing`.
  Documents are processed one at a time by default; set `WORKER_CONCURRENCY` or add worker replicas to process several in parallel (see DECISIONS.md).
- Hybrid search: pgvector plus PostgreSQL full text, fused with weighted RRF in one SQL query, always filtered by tenant, then reordered by a local cross-encoder.
- Answers validated in three layers (JSON, Pydantic schema, quotes grounded in the chunks), with a retry carrying the error and an explicit "not enough information" otherwise. Timeouts and transient errors have bounded retries.
- Tenant isolation: a JWT per tenant, a `tenant_id` filter, and forced Row Level Security under a non-owner role.
- Prompt injection in documents is detected at ingestion; flagged chunks are excluded and the answer warns. Disagreeing sources cap the confidence.
- Chat over SSE with live phases, persisted conversations (search, rename, delete) and follow-up questions; with a real LLM, generated conversation titles and suggested follow-ups; a stopped answer keeps its question to ask again.
- React app: upload with status, document table with state in the URL, PDF viewer with the cited passage highlighted, chat with citations and export, dark mode, accessibility tests.
- OpenTelemetry: one trace from the upload's HTTP request through the Celery task; queries traced with the LLM's latency and tokens, linked to the ingestion traces; JSON logs with `trace_id`.
- Docker Compose for everything, environment-based configuration, reproducible migrations, and CI.

## What is not done

- **OCR:** a scanned PDF fails with `empty_pdf` ("no extractable text").
- **Reranking:** the cross-encoder is English-only and its effect has not been recalibrated into the keyless answer rule (ADR-015).
- **Metrics:** no OpenTelemetry metrics or dashboards (Prometheus or Grafana); quality and cost signals are span attributes and logs.
- **Traces from the browser:** traces start at the API.
- **Product features:** 👍/👎 feedback on answers.
- **Operations:** a periodic sweep of expired leases (an expired lease is taken over when the task is delivered again), a transactional outbox for enqueuing (a crash between the upload's commit and the enqueue leaves the document `pending`), Docker secrets for provider keys, and a pre-built image in a registry.
- **Deleting documents:** there is no endpoint to delete a document with its file and chunks.
- **Users:** auth is one test JWT per tenant; there are no users, passwords, roles or token revocation. The demo sign-in buttons only hand out those JWTs to whoever can reach the API (ADR-016); they are not real authentication.

The reasons, and what each would cost, are in [DECISIONS.md](DECISIONS.md).
