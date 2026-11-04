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

1. Download each `source_url` of `contracts.yaml` into `local-docs/calibration-contracts/` (git ignores `local-docs/`) with the name given in `file`. Some sites block scripted downloads; a browser works.
2. Verify the files and the questions:

   ```bash
   uv run sample_data/calibration/verify_questions.py --pdfs local-docs/calibration-contracts
   ```

   It fails if a hash or a page count does not match, so a changed upstream file is detected instead of silently shifting pages.
