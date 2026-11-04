# Calibration with real contracts

The keyless answer mode quotes the retrieved sentences most similar to the question and says "not enough information" when none is close enough. Where "close enough" lies depends on the embedding model, so the threshold is measured per model on real contracts, and then checked once on different contracts.

## Files

| File | What it is |
| --- | --- |
| `contracts.yaml` | Manifest of the 14 real public contracts: source URL, title, page count, sha256 and set (`calibration` or `eval`) |
| `questions_calibration.yaml` | Questions over the 8 calibration contracts. Thresholds are tuned **only** on these |
| `questions_eval_real.yaml` | Questions over the 6 held-out contracts. Used once, after choosing the thresholds, and for testing by hand |
| `verify_questions.py` | Checks the PDFs against the manifest and every question against the page it cites |

The split is **by document**, not by question, so no tuned value has seen an evaluation document. Contracts that share a template (the two State of Maine repair agreements, the two State of Missouri fuel card contracts) stay in the same set.

Question kinds: `lexical` (answerable, wording close to the clause), `semantic` (answerable, paraphrased), `unanswerable` (topic absent from every document of the set) and `near_miss` (a topic close to one that is present, but the fact asked for is missing). `absent_terms` lists phrases the verifier confirms appear in no document of the set.

## The PDFs are not in the repository

They are public records and terms published by US state and county governments and carriers, but their reuse terms were not reviewed, so only their source is recorded. To reproduce:

1. Download each `source_url` of `contracts.yaml` into `var/calibration-contracts/` (git ignores `var/`) with the name given in `file`. Some sites block scripted downloads; a browser works.
2. Verify the files and the questions:

   ```bash
   uv run sample_data/calibration/verify_questions.py --pdfs var/calibration-contracts
   ```

   It fails if a hash or a page count does not match, so a changed upstream file is detected instead of silently shifting pages.

## Calibrating and evaluating

`backend/scripts/calibrate_extractive.py` reproduces what the app does: pdfplumber extraction and the app's chunking (cached per PDF under `var/calibration-cache/`), the chunks inserted into a throwaway tenant of the running Postgres, the app's hybrid search, and the fake LLM's sentence scoring. The embedding model only runs in the backend image, so run it there, on the Compose network, from the repository root:

```bash
# Optional, on the host: fill the chunk cache (pdfplumber is a regular dependency).
(cd backend && uv run python scripts/calibrate_extractive.py --set calibration --extract-only \
  --data ../sample_data/calibration --pdfs ../var/calibration-contracts --cache ../var/calibration-cache)

docker run --rm --cpus 3 -e OMP_NUM_THREADS=2 -e MKL_NUM_THREADS=2 \
  --network rutaviva_default -e JWT_SECRET=calibration-only-0123456789abcdef \
  -v ./backend/app:/app/app:ro -v ./backend/scripts:/app/scripts:ro \
  -v ./sample_data:/sample_data:ro -v ./var:/work \
  rutaviva-api python scripts/calibrate_extractive.py --set calibration --out /work/calibration_rows.json
```

`--set calibration` compares the decision rules and prints the chosen values; `--set eval` (the held-out run, once) asks every eval question through the answer service with the thresholds of `app/core/similarity.py`. The throwaway tenant is deleted at the end. Embeddings are cached in `var/calibration-cache/` too, so a rerun is quick; the first run takes a while on a shared 4-CPU machine (BGE needs seconds per chunk there). Two PyTorch threads: on the Intel Mac used here, three crashed PyTorch with an illegal instruction on long batches. Results: `results_bge-base-en-v1.5.json` (calibration) and `results_eval_bge-base-en-v1.5.json` (held out); ADR-011 in `DECISIONS.md` sums them up.

The question files use the schema of `sample_data/questions.yaml` (plus the `near_miss` kind and `absent_terms`), so an evaluation harness that runs a questions file against a tenant of the running API can run them too: upload the six `eval` PDFs to the tenant `tenant_eval` and ask `questions_eval_real.yaml`.
