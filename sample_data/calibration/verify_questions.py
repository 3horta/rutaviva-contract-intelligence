# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdf==6.1.1", "pyyaml==6.0.2"]
# ///
"""Check the real-contract question sets against the PDFs they cite.

    uv run sample_data/calibration/verify_questions.py [--pdfs var/calibration-contracts]

Checks, failing on the first set with errors:
- manifest (contracts.yaml): every PDF is present, its sha256 and page count match;
- every question: unique id, known kind/outcome, its tenant matches its set, and every
  cited document belongs to that set (calibration questions never cite eval documents);
- answered: each cited page contains at least one `answer_has_any` string;
- insufficient_evidence: no sources, and no `absent_terms` string appears in ANY page of
  the set's documents (the topic really is missing; near-miss questions name the close
  topic that IS present in `note`).

Text comes from pypdf, page by page (page = physical page, 1-based, as in the app).
Matching ignores case, whitespace and typographic quote/dash variants, because pypdf
sometimes splits words ("co nditions") or keeps curly quotes.
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SETS = {
    "calibration": ("questions_calibration.yaml", "tenant_calibration"),
    "eval": ("questions_eval_real.yaml", "tenant_eval"),
}
KINDS = {"lexical", "semantic", "unanswerable", "near_miss"}
OUTCOMES = {"answered", "insufficient_evidence"}
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text.translate(_TRANSLATE).casefold())


def load_pages(pdfs: Path, manifest: list[dict], errors: list[str]) -> dict[str, list[str]]:
    pages: dict[str, list[str]] = {}
    for entry in manifest:
        path = pdfs / entry["file"]
        if not path.exists():
            errors.append(f"missing PDF {path} (download it from {entry['source_url']})")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            errors.append(f"{entry['file']}: sha256 {digest} != manifest {entry['sha256']}")
            continue
        texts = [p.extract_text() or "" for p in PdfReader(path).pages]
        if len(texts) != entry["pages"]:
            errors.append(f"{entry['file']}: {len(texts)} pages != manifest {entry['pages']}")
        pages[entry["file"]] = [norm(t) for t in texts]
    return pages


def check_set(name: str, manifest: list[dict], pages: dict[str, list[str]]) -> list[str]:
    filename, tenant = SETS[name]
    path = HERE / filename
    if not path.exists():
        return [f"{filename}: not found"]
    docs = {c["file"] for c in manifest if c["set"] == name}
    spec = yaml.safe_load(path.read_text())
    errors: list[str] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}

    for q in spec["questions"]:
        qid = q.get("id", "?")
        where = f"{filename}:{qid}"
        expect = q.get("expect", {})
        if qid in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(qid)
        for key in ("id", "tenant", "question", "kind", "soft", "note", "expect"):
            if key not in q:
                errors.append(f"{where}: missing {key}")
        if q.get("tenant") != tenant:
            errors.append(f"{where}: tenant must be {tenant}")
        if q.get("kind") not in KINDS:
            errors.append(f"{where}: unknown kind {q.get('kind')}")
        outcome = expect.get("outcome")
        if outcome not in OUTCOMES:
            errors.append(f"{where}: unknown outcome {outcome}")
        counts[q.get("kind")] = counts.get(q.get("kind"), 0) + 1

        if outcome == "answered":
            needles = [norm(s) for s in expect.get("answer_has_any", [])]
            if not needles or not expect.get("sources"):
                errors.append(f"{where}: answered needs sources and answer_has_any")
            for source in expect.get("sources", []):
                doc, page = source.get("document"), source.get("page")
                if doc not in docs:
                    errors.append(f"{where}: {doc} is not a {name} document")
                    continue
                if doc not in pages:
                    continue  # already reported as missing/mismatched
                if not isinstance(page, int) or not 1 <= page <= len(pages[doc]):
                    errors.append(f"{where}: {doc} has no page {page}")
                    continue
                if not any(n in pages[doc][page - 1] for n in needles):
                    errors.append(f"{where}: none of answer_has_any is on {doc} p.{page}")
        elif outcome == "insufficient_evidence":
            if expect.get("sources"):
                errors.append(f"{where}: an unanswerable question has no sources")
            absent = expect.get("absent_terms", [])
            if not absent:
                errors.append(f"{where}: unanswerable needs absent_terms")
            for term in absent:
                hits = [
                    f"{doc} p.{i}"
                    for doc in sorted(docs & pages.keys())
                    for i, text in enumerate(pages[doc], start=1)
                    if norm(term) in text
                ]
                if hits:
                    errors.append(f"{where}: absent term {term!r} found in {', '.join(hits[:3])}")

    print(f"{filename}: {len(spec['questions'])} questions {dict(sorted(counts.items()))}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdfs", type=Path, default=REPO / "var/calibration-contracts")
    parser.add_argument("--set", choices=sorted(SETS), action="append")
    args = parser.parse_args()

    manifest = yaml.safe_load((HERE / "contracts.yaml").read_text())["contracts"]
    errors: list[str] = []
    pages = load_pages(args.pdfs, manifest, errors)
    for name in args.set or sorted(SETS):
        errors += check_set(name, manifest, pages)

    for error in errors:
        print("ERROR", error)
    if errors:
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
