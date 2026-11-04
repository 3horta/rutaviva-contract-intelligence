# Decisions

Short ADRs, written when each milestone closes. Each one states the context, the decision,
the alternative that was rejected and the consequences, plus how it is verified.
Answers to the four open questions of the brief, known limits and how generative AI was used
are collected at the end once the related milestones are done.

| # | Decision | Status |
|---|---|---|
| [ADR-001](#adr-001--no-paid-keys-by-default-fail-fast-configuration) | No paid keys by default, fail-fast configuration | Accepted |
| [ADR-007](#adr-007--embeddings-a-real-local-model-at-runtime-a-simulated-one-in-tests) | Embeddings: a real local model at runtime, a simulated one in tests | Accepted |
| [ADR-002](#adr-002--tenant-isolation-column-filter-plus-forced-rls) | Tenant isolation: column filter plus forced RLS | Accepted |
| [ADR-003](#adr-003--duplicates-the-same-pdf-is-never-live-twice-in-a-tenant) | Duplicates: the same PDF is never live twice in a tenant | Accepted |
| [ADR-004](#adr-004--no-document-is-left-stuck-in-processing) | No document is left stuck in `processing` | Accepted (schema done; task logic in progress) |
| [ADR-005](#adr-005--closed-set-of-failure-codes) | Closed set of failure codes | Accepted |
| [ADR-006](#adr-006--ci-fails-instead-of-skipping-the-database-tests) | CI fails instead of skipping the database tests | Accepted |
| [ADR-F01](#adr-f01--chat-sse-read-with-fetch-the-server-as-the-source-of-truth) | Chat: SSE read with fetch, the server as the source of truth | Accepted |
| [ADR-F02](#adr-f02--ci-runs-the-frontend-checks-in-their-own-job) | CI runs the frontend checks in their own job | Accepted |
| [ADR-F03](#adr-f03--route-errors-keep-the-shell) | Route errors keep the shell | Accepted |
| [ADR-F04](#adr-f04--a-strict-content-security-policy-for-the-web-app) | A strict Content-Security-Policy for the web app | Accepted |
| [ADR-F05](#adr-f05--frontend-types-generated-from-a-committed-openapi-document) | Frontend types generated from a committed OpenAPI document | Accepted |

---

## ADR-001 · No paid keys by default, fail-fast configuration

- **Context:** the system must run with no paid keys. Paid providers are optional.
- **Decision:**
  - `Settings` (pydantic-settings) defaults to a fake LLM (`LLM_PROVIDER=fake`) and to a real embedding model that runs locally (`EMBEDDINGS_PROVIDER=local`, see ADR-007). Neither needs a key. This holds in code, in `.env.example` and in Compose, where `.env` is optional.
  - A real provider without its key **stops startup** with a clear message. It never falls back to fake mode silently.
  - Keys are `SecretStr`, and validation errors hide input values (`hide_input_in_errors`). Without that, a misconfigured start printed the key in the error.
  - `EMBEDDING_DIM` must match the schema (`vector(768)`).
- **Rejected:** a silent fallback to fake mode. It would hide configuration mistakes.
- **Consequences:** `/health` reports the active providers and embedding model, never the keys. The only real provider is Gemini. A second LLM (Groq) was dropped from scope.
- **Verified by:** `backend/tests/unit/test_config.py`, `backend/tests/unit/test_health.py`.

## ADR-002 · Tenant isolation: column filter plus forced RLS

- **Context:** procurement teams (tenants) must never see each other's documents.
- **Options and costs:**

  | Option | Risk | Cost |
  |---|---|---|
  | `tenant_id` column + mandatory filter in code | One forgotten `WHERE` leaks data | Lowest |
  | **Column + Row Level Security (chosen)** | Owners and superusers bypass RLS unless it is forced; the tenant must be set in every transaction, in the API and in the worker; harder to debug | One extra role, a policy per table, `set_config` per transaction |
  | Schema or database per tenant | Strongest | Migrations and connections multiplied by N |

- **Decision:**
  - Every tenant table has `tenant_id`, including the child tables (`chunks`, `messages`), so search filters without a join.
  - RLS is `ENABLE` + `FORCE` on those tables. The policy reads `NULLIF(current_setting('app.tenant_id', true), '')::uuid`, so **with no tenant set, nothing is visible** (fails closed).
  - The app connects as `rutaviva_app`, which is neither superuser nor owner. Migrations use the owner.
  - The tenant is set with `set_config('app.tenant_id', …, true)`, scoped to the transaction, so nothing leaks through the connection pool.
  - Composite FKs `(parent_id, tenant_id)` make it impossible for a chunk or message to belong to another tenant than its parent.
- **Rejected:** a filter in code only, because isolation would depend on every query remembering the `WHERE`.
- **Pending measurement:** whether RLS (non-leakproof operators such as `@@` and `<=>`) prevents the GIN or HNSW index from being used. An `EXPLAIN` will be added in the isolation milestone.
- **Verified by:** `backend/tests/integration/test_schema.py`:
  - tenant B sees none of A's rows without any `WHERE`;
  - no tenant set means no rows;
  - writing another tenant's row fails;
  - a child row with a mismatched tenant fails.

## ADR-003 · Duplicates: the same PDF is never live twice in a tenant

- **Context:** uploading the same contract twice (easy in a batch) must not process it twice. A document that failed must still be retryable.
- **Decision:** partial unique index `UNIQUE (tenant_id, sha256) WHERE status <> 'failed'`.
  - The upload checks for a live duplicate and returns it (`200`, `duplicate: true`).
  - The index closes the race between two simultaneous uploads.
  - A failed document does not count, so uploading the file again creates a new document.
- **Rejected:**
  - A plain `UNIQUE (tenant_id, sha256)`: it blocks retrying a failed PDF.
  - An application-only check: two concurrent uploads could both pass it.
- **Verified by:** `test_same_pdf_cannot_be_live_twice`, `test_failed_pdf_can_be_uploaded_again`, `test_same_pdf_in_two_tenants_is_allowed`.

## ADR-004 · No document is left stuck in `processing`

- **Context:** a common failure in task pipelines: retries run out inside an exception handler, or the worker process dies, and the job stays "processing" forever. `acks_late` re-delivers the task, but a transition that only accepts `pending`/`failed → processing` would refuse it.
- **Decision:** three mechanisms, each covering a different failure:
  1. **`try/finally` in the task:** any non-transient error, or retries running out, ends in `failed` with a code.
  2. **Lease:** `processing_started_at`. The atomic transition also accepts a `processing` document whose lease is older than the task time limit. This covers a process that dies, where no `finally` runs.
  3. **Delivery counter:** `attempts`. After the maximum the document is marked `failed` (`worker_lost`), so a PDF that crashes the worker is not re-delivered in a loop.
- **Rejected:** a periodic sweeper (`celery beat`). It is one more service, and the lease already covers re-delivery.
- **Consequences:** a `CHECK` makes the database enforce that a document is `processing` if and only if it has a lease.
- **Verified by:** `test_lease_only_exists_while_processing`. The task tests come with the ingestion milestone.

## ADR-005 · Closed set of failure codes

- **Context:** a free-text failure reason cannot be branched on by the UI or tests, and its shape tends to drift between code paths.
- **Decision:** `failure_code` comes from a closed set (`corrupt_pdf`, `empty_pdf`, `embedding_unavailable`, `timeout`, `worker_lost`, `internal_error`). `failure_detail` holds the human-readable text. The database enforces that a document is `failed` if and only if it has a code, and that the code is one of the set.
- **Verified by:** `test_failure_code_is_closed_and_only_on_failed_documents`.

## ADR-006 · CI fails instead of skipping the database tests

- **Context:** locally, the integration tests skip when Postgres is not running, which is convenient. In CI the same behaviour would make a run green with zero isolation tests executed.
- **Decision:**
  - CI runs a `pgvector/pgvector:pg16` service.
  - It runs the migration cycle `upgrade → downgrade → upgrade` and `alembic check` (no drift between models and migrations).
  - It runs pytest with `REQUIRE_DB=1`, which turns those skips into failures.
  - A second job runs gitleaks over the full history.
- **Verified by:** with `REQUIRE_DB=1` and an unreachable database, the integration tests error instead of skipping.

## ADR-007 · Embeddings: a real local model at runtime, a simulated one in tests

- **Context:** the brief asks for a fake or deterministic embeddings provider so that the system runs without paid keys, and for simulated embeddings in the automated tests. A purely lexical fake (the hashing trick) runs anywhere, but it only matches shared words: "exit the agreement early" never finds "terminate for convenience".
- **Decision:**
  - **At runtime:** `BAAI/bge-base-en-v1.5` (768 dimensions, the schema's; trained for retrieval; English; MIT licence) through **sentence-transformers** on PyTorch. It runs in-process with no API and no key, and its weights are baked into the image (no download at runtime).
  - Same model, library and CPU give the same vector for the same text, so the API (queries) and the worker (chunks), two containers of one image, agree.
  - Queries carry the instruction BGE recommends; passages carry none.
  - **In tests:** the simulated embedder (hashing trick over words and character trigrams, `hashlib`, L2-normalised). It is deterministic across processes and lexically meaningful, which is enough to test hybrid SQL, tenant isolation and the pipeline.
- **Rejected:**
  - **fastembed (ONNX):** it would keep the API image light, but it adds a second inference runtime next to the PyTorch that Docling already needs.
  - **A 384-dim model:** it would need a schema migration and gives lower quality.
  - **Models that need remote code:** not reproducible or auditable.
- **Consequences:**
  - One image for API and worker. PyTorch was already there for Docling, so disk and build barely change, but the API uses more memory and starts a few seconds later.
  - PyTorch has no wheels for macOS on Intel, so the model only runs in Docker and CI.
- **Verified by:**
  - `tests/contract/test_fake_embeddings.py`: dimension, unit norm, similarity and determinism across processes with different hash seeds.
  - `tests/contract/test_local_embeddings.py`, run in CI with `REQUIRE_ML=1`: dimension, norm, same vector twice, and a paraphrase that beats unrelated text.
  - Retrieval quality with the real model: the verification question set (`make eval`).

## ADR-F01 · Chat: SSE read with fetch, the server as the source of truth

- **Context:** answers arrive as Server-Sent Events from `POST /query/stream`. `EventSource` can only GET and cannot send the `Authorization` header.
- **Decision:**
  - `src/api/queryStream.ts` POSTs with `fetch`, reads the `ReadableStream` and parses the `event:`/`data:` blocks itself (CRLF, split chunks, comments, unknown events ignored). It resolves with the `result`, rejects with an `ApiError` on an `error` event or a stream that ends without either. The UI never sees SSE details.
  - The server is the source of truth: a finished answer is written into the cached conversation (TanStack Query) exactly as stored; only the turn in progress lives in component state (`useChat`).
  - `/chat/:conversationId?` is one route, so the page stays mounted when a new chat gets its id (`navigate(..., { replace: true })` on the `conversation` event).
  - Stop aborts the fetch. The backend keeps working and stores the turn if it completes, so the page reloads the conversation 1.5 s later and replaces the "stopped" note if the turn was saved.
  - Answers are rendered from a parsed Markdown subset (paragraphs, lists, bold, inline code) as React elements; `dangerouslySetInnerHTML` stays banned by lint.
  - Leaving the page or switching tenant aborts the stream (the page is remounted per tenant; the query cache is cleared).
- **Rejected:** a global chat store that keeps answering after leaving the page (as the reference does): more state for little gain, since the backend stores the turn anyway.
- **Left out:** feedback buttons, suggested follow-ups, dictation and charts: the backend has no support for them.
- **Verified by:** `queryStream.test.ts` (split chunks, several events per chunk, CRLF, missing result, error event), `markdown.test.ts`, `grouping.test.ts`.

## ADR-F02 · CI runs the frontend checks in their own job

- **Decision:** a `frontend` job on Node 22 (the version of the Docker build), with the npm cache keyed on `frontend/package-lock.json`: `npm ci` → lint → typecheck → tests → build. It runs in parallel with the backend job and does not need a database or a running API (the tests use the in-memory mock API).
- **Consequence:** a broken build or a type error fails the PR even when only backend code changed in the same push.

## ADR-F03 · Route errors keep the shell

- **Decision:** two `errorElement`s. One on a pathless route that wraps every page, so a page that throws shows `RouteErrorPage` inside the shell and the sidebar keeps working. One on the root, for when the shell itself fails. A `404` route response reads "Page not found"; anything else reads "Something went wrong", with "Back to the dashboard" and "Reload". The error goes to the console only in development; users never see a stack trace.
- The routes live in `src/routes.tsx`, shared by the browser router and the tests' memory router.

## ADR-F04 · A strict Content-Security-Policy for the web app

- **Decision:** nginx sends, on every app page (`location /`): `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; worker-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'`, plus `Permissions-Policy: camera=(), microphone=(), geolocation=()`. There is no `'unsafe-inline'` and no `'unsafe-eval'`.
- **The inline theme script** (it sets the theme before the first paint, so dark mode never flashes light) moved to `public/theme-init.js`, a blocking `<script src>` served from `'self'`. A sha256 hash would also work, but it breaks silently every time someone edits the script. A file has no such trap.
- **Relaxations, each one needed:**
  - `img-src data: blob:`: PDF.js paints page images from data and blob URLs.
  - `worker-src blob:`: PDF.js may start its worker from a blob URL. The worker file itself is same-origin (`/assets/pdf.worker…mjs`).
- **Why nothing else is needed:**
  - React sets `style` through the CSSOM (`element.style.x = …`), which `style-src 'self'` allows. Only `style="…"` written in HTML markup and `<style>` elements are blocked, and the build emits neither.
  - PDF.js probes for `eval` (`new Function("")`) only when `isEvalSupported` is on. The viewer turns it off.
  - The headers are added in `location /` because nginx's `add_header` in a location replaces the server-level ones. The JSON API responses do not need a CSP.
- **Proxy re-resolves the API:**
  - A literal `proxy_pass http://api:8000` is resolved once at startup. After the api container is recreated (new IP), nginx answered 502 until restarted.
  - `proxy_pass` now takes a variable, resolved per request through Docker's DNS (`resolver 127.0.0.11 valid=10s`).
  - An explicit `rewrite` strips `/api`, since a variable `proxy_pass` no longer does it. The SSE location stays unbuffered.
- **Verified by:**
  - `curl -I` on the built image (`docker build -f frontend/Dockerfile`, run on the Compose network) shows both headers.
  - The built `index.html` has no inline `<script>` or `<style>`.
  - Headless Chrome loads the pages with no CSP violations in the console.
  - A full click-through in a scripted browser (viewer, chat stream) was not possible here: the browser container could not reach the npm registry. That check is listed as pending.

## ADR-F05 · Frontend types generated from a committed OpenAPI document

- **Context:** the frontend types were written by hand, so a backend change could only show up at runtime.
- **Decision:**
  - The API's OpenAPI document is committed as `frontend/openapi.json`.
  - `npm run gen:types` (openapi-typescript 7.8.0) turns it into `src/api/schema.d.ts`.
  - `src/api/types.ts` keeps the names the app uses and derives them from that schema (`DocumentOut = components["schemas"]["DocumentOut"]`, …). A contract change therefore surfaces as type errors where it matters.
  - Still written by hand: what OpenAPI cannot describe (the SSE events, Problem Details) and one refinement (the bbox is a 4-tuple, the schema says `number[]`).
- **Why a committed file and not the live `/openapi.json`:**
  - CI and fresh clones need no running backend, database or ML stack.
  - Every contract change shows up as a reviewable diff in the PR.
- **Regenerating:** with the API running, `curl -s http://localhost:8000/openapi.json | python3 -m json.tool --indent 2 > frontend/openapi.json`, then `npm run gen:types` in `frontend/`. Without a running API, the same JSON comes from `create_app().openapi()` in the backend environment (it needs `JWT_SECRET` set).
- **CI:** the frontend job re-runs `gen:types` and fails if `schema.d.ts` differs from what the committed `openapi.json` produces. It does not check `openapi.json` against the backend code: that needs the backend's Python environment, which is the backend job's (out of this change's scope). That check is listed as debt.
