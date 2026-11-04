# rutaviva-contract-intelligence

Contract intelligence platform for RutaViva.

## Structure

- `backend/` — FastAPI API, ingestion, hybrid retrieval, generation, Celery worker
- `frontend/` — Vite + React + TS
- `infra/` — OpenTelemetry Collector and Nginx configuration
- `sample_data/` — sample PDF generator and evaluation questions
- `notes/` — decisions and AI usage log

## Frontend

**Stack:**
- React 18 + TypeScript (strict, `noUncheckedIndexedAccess`) on Vite.
- TanStack Query for server state.
- React Router 6 for URLs.
- Plain CSS Modules over design tokens (`src/styles/tokens.css`, light and dark).
- PDF.js for the viewer; jsPDF for the chat export (both loaded lazily).

There is no UI kit: the few primitives live in `src/components/ui`. That keeps the bundle small and every line of styling readable.

**Structure (by feature):**

```
frontend/src/
  api/          client (auth header, Problem Details → ApiError), endpoints, SSE stream, types
  auth/         token store per tenant, token screens, tenant switcher
  components/ui buttons, badges, panels, states (loading / empty / error), fields
  features/     dashboard, documents (list, detail, viewer), upload, chat
  layout/       app shell, route error page
  mocks/        in-memory API for VITE_USE_MOCKS=true (also used by the tests)
  test/         renderApp helper and whole-app tests
```

**Data flow:**
1. `authStore` (one token per tenant, in `sessionStorage`).
2. `apiRequest` adds `Authorization`, turns any non-2xx into an `ApiError` and flags a 401.
3. React Query hooks per feature (`features/*/queries.ts`) cache and poll.
4. Components.

Answers stream from `POST /query/stream`. `api/queryStream.ts` reads the Server-Sent Events with `fetch`, because `EventSource` cannot POST nor send headers. The UI never sees the SSE details.

**Run it** (in `frontend/`):

```bash
npm ci
npm run dev          # http://localhost:5173, proxies /api to the API on :8000
npm run dev:mocks    # no backend: the API is simulated in the browser ("Use demo tokens")
```

In Docker, `make up` serves the build with Nginx on `http://localhost:8080` (same origin for `/api`, strict CSP).

In mock mode the chat recognises a few test questions:
- `outage` gives a 503 and `timeout` a 504;
- a question about the weather gets "not enough evidence";
- a question that spans several suppliers carries a warning;
- a question that mentions `instructions` shows an excluded fragment.

**Types from the API:**
- `src/api/schema.d.ts` is generated from the committed `frontend/openapi.json`, and `src/api/types.ts` derives the app's names from it.
- After a backend change, with the API running:
  - `curl -s http://localhost:8000/openapi.json | python3 -m json.tool --indent 2 > frontend/openapi.json`
  - then `npm run gen:types`.
- CI fails if `schema.d.ts` is out of date.

**Tests:**
- Run `npm test` (Vitest + Testing Library + jest-axe), with `npm run lint` and `npm run typecheck` alongside.
- Unit tests cover the SSE parser, the Markdown parser, table URL state, upload queue, grouping and token contrast (WCAG AA in both themes).
- `src/test/` renders the whole app over the mock API: sign-in, 401, tenant isolation, upload, the chat's states and accessibility.

**Key decisions** (details in `DECISIONS.md`, ADR-F01 to ADR-F09):
- **State in the URL:** table page, sort, search and filter, and the open conversation (`/chat/:id`). Back and shared links restore them.
- **Conditional polling:** lists and details poll only while a document is still pending or processing.
- **Tenant isolation:**
  - Switching tenant empties the query cache and remounts the pages, which also aborts uploads and the answer in progress.
  - Another tenant's document is a "not found".
- **Mocks at the fetch level:** the real client, error handling and queries run unchanged in the demo and in the tests.
- **Markdown without `innerHTML`:** answers are parsed into data and drawn as React elements. `dangerouslySetInnerHTML` is banned by lint.
- **CSP:** `script-src 'self'` and `style-src 'self'`, with no `unsafe-inline` and no `unsafe-eval`. The pre-paint theme script is a file, not inline.

**Left out, and why:**
- 👍/👎 feedback, suggested follow-ups, dictation and charts in answers: the backend does not support them.
- Playwright end-to-end tests and error reporting (Sentry): out of scope for the test.
- i18n: the UI is English only.

## Evaluation

Quality is checked by behaviour, not by scores (see ADR-014 in `DECISIONS.md`). With the stack up (`make up`):

```bash
make eval                                    # every question in sample_data/questions.yaml
make eval ARGS="--kinds isolation,injection --repeat 3"
make eval ARGS="--only carrier-detention-rate,customs-brokerage-absent --no-upload"
make eval-real PDFS=/path/to/pdfs            # held-out real contracts (one subfolder per tenant)
```

The harness uploads each tenant's sample PDFs (duplicates are fine), waits for them to be processed, asks every question in a fresh conversation and checks the outcome, the cited page, required and forbidden strings, that no cited or retrieved chunk belongs to another tenant, the confidence cap and the warnings. It also checks that every number in an answer appears in a cited quote. It prints one line per case (`PASS`, `FAIL`, `WARN` for soft cases, `FLAKY` for mixed results over `--repeat`), a summary per kind and the p50/p95 latency, and exits 1 if a non-soft case fails.
