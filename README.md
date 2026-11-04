# rutaviva-contract-intelligence

Contract intelligence platform for RutaViva.

## Structure

- `backend/` — FastAPI API, ingestion, hybrid retrieval, generation, Celery worker
- `frontend/` — Vite + React + TS
- `infra/` — OpenTelemetry Collector and Nginx configuration
- `sample_data/` — sample PDF generator and evaluation questions
- `notes/` — decisions and AI usage log

## Evaluation

Quality is checked by behaviour, not by scores (see ADR-014 in `DECISIONS.md`). With the stack up (`make up`):

```bash
make eval                                    # every question in sample_data/questions.yaml
make eval ARGS="--kinds isolation,injection --repeat 3"
make eval ARGS="--only carrier-detention-rate,customs-brokerage-absent --no-upload"
make eval-real PDFS=/path/to/pdfs            # held-out real contracts (one subfolder per tenant)
```

The harness uploads each tenant's sample PDFs (duplicates are fine), waits for them to be processed, asks every question in a fresh conversation and checks the outcome, the cited page, required and forbidden strings, that no cited or retrieved chunk belongs to another tenant, the confidence cap and the warnings. It also checks that every number in an answer appears in a cited quote. It prints one line per case (`PASS`, `FAIL`, `WARN` for soft cases, `FLAKY` for mixed results over `--repeat`), a summary per kind and the p50/p95 latency, and exits 1 if a non-soft case fails.
