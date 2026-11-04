# Decisions

Short ADRs, written when each milestone closes. Each one states the context, the decision,
the alternative that was rejected and the consequences, plus how it is verified.
Answers to the four open questions of the brief, known limits and how generative AI was used
are collected at the end once the related milestones are done.

| # | Decision | Status |
|---|---|---|
| [ADR-001](#adr-001--no-paid-keys-by-default-fail-fast-configuration) | No paid keys by default, fail-fast configuration | Accepted |
| [ADR-002](#adr-002--tenant-isolation-column-filter-plus-forced-rls) | Tenant isolation: column filter plus forced RLS | Accepted |
| [ADR-003](#adr-003--duplicates-the-same-pdf-is-never-live-twice-in-a-tenant) | Duplicates: the same PDF is never live twice in a tenant | Accepted |
| [ADR-004](#adr-004--no-document-is-left-stuck-in-processing) | No document is left stuck in `processing` | Accepted |
| [ADR-005](#adr-005--closed-set-of-failure-codes) | Closed set of failure codes | Accepted |
| [ADR-006](#adr-006--ci-fails-instead-of-skipping-the-database-tests) | CI fails instead of skipping the database tests | Accepted |
| [ADR-007](#adr-007--embeddings-a-real-local-model-at-runtime-a-simulated-one-in-tests) | Embeddings: a real local model at runtime, a simulated one in tests | Accepted |
| [ADR-008](#adr-008--text-extraction-pdfplumber-by-default-docling-optional) | Text extraction: pdfplumber by default, Docling optional | Accepted |
| [ADR-009](#adr-009--cpu-inference-load-models-once-bound-the-threads) | CPU inference: load models once, bound the threads | Accepted |
| [ADR-010](#adr-010--chat-backend-streamed-phases-persisted-turns-resolved-follow-ups) | Chat backend: streamed phases, persisted turns, resolved follow-ups | Accepted |
| [ADR-013](#adr-013--untrusted-content-flag-exclude-and-warn-disagreeing-sources-degrade-confidence) | Untrusted content: flag, exclude and warn; disagreeing sources degrade confidence | Accepted |
| [ADR-014](#adr-014--quality-by-behaviour-an-evaluation-harness-over-a-small-question-set) | Quality by behaviour: an evaluation harness over a small question set | Accepted |
| [ADR-F01](#adr-f01--chat-sse-read-with-fetch-the-server-as-the-source-of-truth) | Chat: SSE read with fetch, the server as the source of truth | Accepted |
| [ADR-F02](#adr-f02--ci-runs-the-frontend-checks-in-their-own-job) | CI runs the frontend checks in their own job | Accepted |
| [ADR-F03](#adr-f03--route-errors-keep-the-shell) | Route errors keep the shell | Accepted |
| [ADR-F04](#adr-f04--a-strict-content-security-policy-for-the-web-app) | A strict Content-Security-Policy for the web app | Accepted |
| [ADR-F05](#adr-f05--frontend-types-generated-from-a-committed-openapi-document) | Frontend types generated from a committed OpenAPI document | Accepted |
| [ADR-F06](#adr-f06--whole-app-tests-over-the-mock-api) | Whole-app tests over the mock API | Accepted |
| [ADR-F07](#adr-f07--accessibility-checked-by-tests-contrast-checked-on-the-tokens) | Accessibility checked by tests; contrast checked on the tokens | Accepted |
| [ADR-F08](#adr-f08--frontend-code-review-what-was-fixed-what-is-debt) | Frontend code review: what was fixed, what is debt | Accepted |

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

## ADR-008 · Text extraction: pdfplumber by default, Docling optional

- **Context:** the brief suggests Docling, and it was the first choice (layout model, reading order, table structure, element coordinates). Measured on the target machine (Docker with 4 CPUs and 4 GB), with the model loaded once and the table-structure stage off, it still took **~70 s per page**: a 4-page sample contract needed 292 s just to convert, beyond the 240 s task soft limit.
- **Decision:**
  - `TEXT_EXTRACTOR=pdfplumber` is the default. It reads the PDF text layer with coordinates: **~1 s for a 3-page contract**, no models, on every platform.
  - Lines in the top/bottom page margins (running headers and footers) are dropped.
  - Blocks split at paragraph gaps, numbered clauses and headings (short all-caps lines or SECTION/ARTICLE/EXHIBIT…), which chunking uses as the section name.
  - `TEXT_EXTRACTOR=docling` keeps Docling, models baked into the image, OCR off.
- **Rejected:**
  - **Docling by default with higher time limits:** minutes per document for whoever runs the demo.
  - **Raising the default limits for everyone:** hides the cost instead of choosing.
- **Consequences:**
  - Contracts with complex layouts (multi-column, real tables) are extracted in text order without table structure, a known limit.
  - Scans still end `empty_pdf`, because there is no OCR.
  - pypdfium2 (a pdfplumber dependency) is pinned to the newest release with a macOS 12 Intel wheel.
- **Verified by:** `tests/unit/test_pdfplumber_extract.py`: page counts of every sample, a known clause on its page with a top-left box, headers and footers dropped, headings detected, corrupt → `corrupt_pdf`, scan → `empty_pdf`. These run on any machine. The Docling tests run in Linux/CI (`REQUIRE_DOCLING=1`).

## ADR-009 · CPU inference: load models once, bound the threads

- **Context:** the first end-to-end runs on the target machine were slow, and part of it was avoidable:
  - two concurrent first requests both loaded the embedding model;
  - the worker built a new Docling extractor for every task, reloading the layout model for every PDF;
  - PyTorch uses every core in every process by default, so the API and the worker fought for the same 4 CPUs.
- **Decision:**
  - The embedding model is created under a lock, once per process.
  - The extractor is cached per worker process.
  - `OMP_NUM_THREADS`/`MKL_NUM_THREADS` = 2 for the API and 2 for the worker (`API_TORCH_THREADS`, `WORKER_TORCH_THREADS`).
  - Worker concurrency is 1 (`WORKER_CONCURRENCY`): CPU-bound tasks in parallel only slow each other down.
- **Rejected:** more worker processes, which oversubscribe the CPU.
- **Known limit:** the keyless "LLM" ranks the sentences of the retrieved chunks with the embedding model at query time, so answers take seconds to tens of seconds on a small CPU. Embedding sentences at indexing time is the next step if it matters.

## ADR-010 · Chat backend: streamed phases, persisted turns, resolved follow-ups

- **Context:** the chat needs streaming, a history per conversation and follow-up questions ("and the cap?") that only make sense with the previous turn. No answer text may be shown before it is validated.
- **Decision:**
  - **Stream:** `POST /query/stream` (SSE) sends `conversation`, then `phase` events (`contextualizing` when there is history, then `searching`, `drafting`, `validating`), then a single validated `result`, or an `error` with the Problem Details shape. Auth, validation and a foreign `conversation_id` fail before the stream starts, with normal HTTP status codes.
  - **Persistence:** the question and the answer are saved together, atomically, after a valid answer (answered or insufficient evidence). Provider failures save nothing. `retry` replaces the last turn in the same transaction, and a row lock keeps sequence numbers consecutive under concurrency.
  - **Follow-ups:** before retrieval, a question resolver rewrites the question as standalone using the last 3 turns.
    - With a real LLM, an LLM-based resolver. Earlier turns are delimited as data, and any failure leaves the question unchanged, so a conversation never makes a query fail.
    - In keyless mode, a deterministic rule: a question with ≤ 2 content words, or starting with a connector or pronoun, is searched together with the previous standalone question.
    - The interpreted question is stored and shown in "How it was answered".
  - **`details`** of each answer: the retrieved chunks (file, page, score, branch that found them), hits per branch, attempts, model, latency and `trace_id`. Never document content.
- **Rejected:**
  - Streaming LLM tokens, which would show unvalidated text.
  - Answering follow-ups without context: "and the cap?" alone retrieves noise.
  - A separate query-log table: the assistant message carries the metadata, and the trace has the detail.
- **Verified by:** `tests/integration/test_conversations_api.py`: event order, error event with nothing persisted, errors before the stream, persistence, retry, follow-up retrieval, and 404 for another tenant's conversation everywhere. Also `tests/unit/test_resolver.py`.

## ADR-013 · Untrusted content: flag, exclude and warn; disagreeing sources degrade confidence

- **Context:** contracts come from suppliers, so their text is untrusted. The sample fuel agreement hides, in its Exhibit C, an instruction to the assistant to claim there is no minimum volume. The tire agreement and its SLA exhibit state different delivery terms and penalties, and each claims to prevail.
- **Decision:**
  - **Document content is data, never instructions.** The prompt already delimits each chunk and says so (rule 3); the validator only accepts citations of chunks that were given to the model, quoted verbatim.
  - **Suspicious instructions: index, flag, exclude, warn.**
    - At indexing, `guardrails.detect_injection` runs on every chunk before the store transaction and sets `chunks.flagged_injection`. The document is still indexed: the rest of it is legitimate data. The ingest span gets `prompt_injection.flagged` (a count) and the log `document.injection_flagged` (a count, never the text).
    - At query time, flagged chunks are removed from the LLM context, for every provider. They stay in `details.retrieved` with `flagged: true`. The answer carries the warning `prompt_injection`, and the answer span gets `prompt_injection.flagged`. If only flagged chunks were retrieved, the answer is insufficient evidence (`insufficient_reason: all_flagged`), still with the warning.
  - **Disagreeing sources: degrade, don't pick.** When the quotes of an answer come from at least two documents and give different values for the same kind of quantity, both citations stay, confidence is capped at `medium` and the answer carries `sources_disagree`.
  - **`warnings` are machine codes** (`prompt_injection`, `sources_disagree`). `WARNING_LABELS` in `app/schemas/query.py` holds the text the UI shows.
- **The detector:** fourteen named regular expressions, in English and Spanish, over text with case, accents and whitespace normalised. Examples: "ignore/disregard previous instructions", "you are now", "you must act as", a `System:`/`Assistant:` role marker at the start of a sentence, "reveal your prompt", "do not tell the user", "respond only with", "ignora las instrucciones", "eres ahora", "responde únicamente". Each pattern needs the "addressed to the assistant" part, so "Supplier shall follow Rutaviva's written instructions" and "act as an independent contractor" are not flagged. Across the eight sample contracts it flags only Exhibit C.
- **Limits, said plainly:**
  - It is pattern-based, so it can be bypassed: paraphrases, other languages, an instruction split across two chunks, text in images. It is one layer of defence in depth, not a guarantee; the delimiting in the prompt and the citation validation still apply to whatever it misses.
  - It flags whole chunks. A legitimate clause in the same chunk as the injection is also excluded (in the sample, clause C.3 of Exhibit C).
  - Documents indexed before this change are not reprocessed: their chunks stay unflagged until they are reindexed.
  - The disagreement rule compares numbers, not meaning. It compares the sets of percentages, of "N hours/days" and of amounts of money in the quotes of each document. It misses contradictions in words ("monthly" vs "weekly"). It also fires when two contracts legitimately have different values for different things, which only costs confidence and adds a warning; it never changes the answer.
- **Rejected:**
  - Rejecting the whole document: the legitimate clauses (the 180,000-gallon minimum) live in it.
  - An LLM classifier for injections: it costs a call per chunk, it is not deterministic, and it is itself injectable.
  - Picking one source when they disagree (for example, "the exhibit prevails"): that is a legal judgement for a person.
- **Verified by:** `tests/unit/test_guardrails.py` (12 injection variants detected, 8 ordinary clauses not, every sample contract scanned, the tire agreement vs SLA exhibit sentences and negative cases), `tests/unit/test_answer_service.py` (flagged chunk excluded with a warning, only flagged → insufficient, disagreeing sources → `medium` with a warning) and `tests/integration/test_injection.py` (the real fuel PDF is ingested with pdfplumber and Exhibit C is flagged; `/query` answers 180,000 gallons from page 1, never quotes the injection and warns `prompt_injection`).

## ADR-014 · Quality by behaviour: an evaluation harness over a small question set

- **Context:** there is no large labelled set to compute retrieval or answer scores with, and a score over 21 questions would mostly measure noise. The question is still "how do we know it answers well?".
- **Decision:** check **behaviour**, not scores. `backend/scripts/eval.py` runs the question set (`sample_data/questions.yaml`) against the **running API**, the same path a user takes, and checks what an analyst would check by hand:
  - **Per case**, from its `expect` block: the outcome (`answered` / `insufficient_evidence`); at least one citation with the expected file and page; the answer contains one of `answer_has_any` and none of `answer_lacks` (case-insensitive); a confidence cap (`max_confidence`, for contradictions); required `warnings` (`prompt_injection`).
  - **Leak check** (`results_in.tenant`): every cited document and every retrieved chunk in `details.retrieved` must be one of the asking tenant's documents, as listed by its own `GET /documents`. A chunk of another tenant fails the case even if it was only retrieved and never cited.
  - **Global checks on every case:** a non-empty answer; no internals in the answer text (UUIDs, tracebacks, SQL, `<document>` context tags); for answered cases, every number in the answer appears in the question or in a cited quote, after normalising the format ("180,000" = "180000", "1,5" = "1.5", "75.00" = "75"). That is a cheap hallucination check: the quotes were already verified against the chunks by the API.
  - **Stability:** `--repeat N` asks each case N times, each in a fresh conversation. Mixed results are `FLAKY`.
  - **Status and exit code:** `PASS`, `FAIL`, `WARN` (a failed `soft: true` case: the semantic paraphrases) or `FLAKY`. The run exits 1 if a non-soft case is `FAIL` or `FLAKY`: a hard case must be right and stable. Soft cases never fail the run.
  - **Setup:** for each tenant in the file the script signs a token with the API's key (`issue_token`, tenant looked up in the database), uploads that tenant's PDFs (`--pdfs`, one subfolder per tenant; a duplicate returns the existing document), and waits until each document is `ready` or `failed` (failed ones are reported, not counted as case failures). `--create-tenants` adds missing tenants with the owner URL, for a held-out set such as `tenant_eval`.
- **Layout:** the checks are pure functions in `scripts/eval_lib.py`; the HTTP calls go through a four-method `Api` protocol, implemented with httpx in `eval.py` and with a dict in the tests. The harness stays in `scripts/` and not in `app/`: it is tooling, never imported by the API or the worker, and `make eval` can mount the current `scripts/` into the existing image, so editing the harness or the questions needs no rebuild.
- **How to run:** with the stack up, `make eval` (all cases) or `make eval ARGS="--only carrier-detention-rate --repeat 3"`. Other flags: `--kinds lexical,isolation`, `--json out.json`, `--no-upload`, `--ingest-timeout`. The held-out real-contract set: `make eval-real PDFS=/path/to/pdfs` (questions in `sample_data/calibration/questions_eval_real.yaml`, which may use the extra kinds `near_miss` and `absent_terms`; any kind is accepted). From the host it also runs as `cd backend && uv run python scripts/eval.py`, given `JWT_SECRET` (the default key lives in a Docker volume the host cannot read).
- **Rejected:**
  - Scores (recall@k, MRR, LLM-as-judge) over 21 questions: too few cases for a number to mean anything, and a judge adds a second model to trust. The behaviour checks are exact and explain every failure in one line.
  - Calling the services in-process: it would skip the HTTP layer, the auth, RLS and the worker, which are the parts most likely to break isolation.
- **Limits:**
  - The set is small and hand-written; it proves the designed behaviours (isolation, refusal, contradiction, injection), not general quality.
  - `sources` passes when one expected page is cited; for a contradiction it does not require both sides.
  - The number check can flag a correct answer that adds a number of its own (a section number not in the quote), and it cannot see a wrong figure that also appears in the quote.
  - String checks accept any listed variant: a paraphrase that drops the figure fails, a wrong answer that happens to contain one passes.
  - Uploading again re-creates the sample documents that are meant to fail (a failed document does not block a new upload of the same file).
- **Verified by:** `tests/unit/test_eval_lib.py`: each check passing and failing, number normalisation, the leak check on citations and on retrieved-only chunks, soft to `WARN`, repeat to `FLAKY`, the exit code, document polling with a timeout, and loading the sample set.

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

## ADR-F06 · Whole-app tests over the mock API

- **Decision:** `src/test/renderApp.tsx` renders the real routes with a memory router, a fresh `QueryClient` (no retries, bound to the same tenant-switch clearing as the app's), the demo tokens and the in-memory mock API with its latency off (`installMockApi({ instant: true })`). The tests drive the app the way a user does (labels, roles, text) and assert on what is shown and on the URL.
- **Covered:**
  - sign-in and 401;
  - the documents table's search and filter in the URL;
  - upload (a non-PDF never reaches the API; a PDF is sent);
  - tenant switch (no documents nor conversations of the other tenant);
  - chat: answer, badge, citation navigation, insufficient evidence, warning, Stop, 504 with Retry;
  - Markdown never producing HTML elements;
  - a route that throws.
- **Rejected:** mocking `fetch` per test or MSW. The mock API already speaks the contract at the fetch level and is what the demo runs on, so the tests exercise the same code. MSW would also have been a new dependency.
- **Bugs these tests found:**
  - A new conversation was fetched before the backend had stored it: a 404 replaced the answer in progress. Fixed: the query waits for the first turn.
  - The mock upload used `Blob.arrayBuffer`, which jsdom lacks. It now reads files with `FileReader`.
- **Cost:** the tests wait up to 10 s per query (`asyncUtilTimeout`) and 30 s per test. That is generous, but a full-app test in jsdom on a slow CI runner needs it.

## ADR-F07 · Accessibility checked by tests; contrast checked on the tokens

- **Decision:**
  - `src/test/a11y.test.tsx` runs axe (jest-axe 10.0.0) on the dashboard, documents, document detail and an answered chat, once their data is on screen. It fails on any serious or critical violation.
  - axe cannot measure contrast in jsdom (there is no layout), so `src/styles/contrast.test.ts` computes the WCAG ratio of every text/background token pair the components use, in both themes. Translucent dark-theme backgrounds are composited over the surface first. Every pair must reach 4.5:1.
- **Found and fixed:** in the light theme, `--rv-muted` (3.8–4.2:1), `--rv-success` on its soft background (4.46:1) and `--rv-warning` (3.9–4.1:1) were below AA. Each was darkened by the smallest step that passes: `#636d7f`, `#167d48`, `#a5590e`. The dark theme already passed.
- **Responsive:**
  - Reviewed in the CSS: every page collapses to one column (dashboard and detail below 1000 px, chat below 900 px with the history folded behind a "History" button). Wide tables scroll inside their own wrapper, so the page itself never scrolls sideways.
  - Not verified with screenshots at 375 and 1280 px: no automated browser could run in this environment. That check is pending.
- **States:** every page uses the same `LoadingState`, `StateBlock` (empty, not found) and `ErrorAlert` (with the trace id) pieces. "No permission" is the shell's token screens (401). Another tenant's document or conversation is a 404, shown as "not found", exactly like a missing one.

## ADR-F08 · Frontend code review: what was fixed, what is debt

- **Reviewed:** all of `frontend/src`, looking for:
  - effects without cleanup;
  - requests without an `AbortSignal`;
  - races on tenant or page switches;
  - unstable keys;
  - duplicated state;
  - `any` or needless casts;
  - components over ~200 lines.
- **Fixed (real bugs):**
  - A new conversation was fetched before the backend had stored it, so a 404 replaced the answer in progress (found by the Task 5 tests; separate commit).
  - Opening another conversation after scrolling up kept "not following", so it did not open at its end. An export error also stayed on screen across conversations.
  - A comment in `useTheme` still pointed to the inline script that the CSP work moved to `public/theme-init.js`.
- **Checked and fine:**
  - Every effect cleans up (timers, `ResizeObserver`, PDF.js tasks, blob URLs, the upload queue, the chat stream).
  - Every query passes React Query's `signal`.
  - Tenant switches clear the cache and remount the pages, so in-flight answers for the old tenant land in removed queries. A 401 only flags the token it was sent with.
  - The casts are `noUncheckedIndexedAccess` narrowing or parsed JSON. There is no `any`.
  - The index keys in `Markdown` are over text that is parsed again on every render and never reordered.
- **Debt (not changed: it works, and a change would be a refactor):**
  - `ChatPage` (~220 lines) and `ConversationSidebar` (~220 lines) are past the 200-line mark. The logic is already out of the view (`useChat`, `queries.ts`, `grouping.ts`). A `ConversationItem` component would be the next split.
  - `DocumentDetailPage` (~250 lines) mixes the KPIs and the viewer frame; it could split along those lines.
  - SSE payloads are cast to their type, not validated. They come from our own API, and a schema validator would be a new dependency.
  - "Open PDF" is not aborted when leaving the page mid-download. The result simply goes into the cache.
  - Rename and delete are mutations without an abort signal, which is normal for writes.
  - `openapi.json` is not checked against the backend code in CI (see ADR-F05).
  - The mock's `delay` adds an abort listener it never removes (mock only).
