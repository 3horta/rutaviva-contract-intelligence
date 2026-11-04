"""Calibrate, and then evaluate once, the "not enough information" rule of the fake LLM.

The keyless fake LLM (`app/providers/llm/fake.py`) quotes the retrieved sentences most
similar to the question and declares insufficient evidence when nothing is close enough.
"Close enough" depends on the embedding model, so it is measured here on real contracts
(`sample_data/calibration/`), reproducing the app as closely as practical:

  1. extract each PDF with pdfplumber and chunk it with the app's settings (cached per
     PDF as JSON under `--cache`, so a rerun only re-embeds and scores);
  2. insert the chunks into a throwaway tenant of the running Postgres (owner role) and
     retrieve with the app's hybrid search (`app/retrieval/hybrid.search`, app role, RLS);
  3. score every quotable sentence of the retrieved chunks as the fake LLM does.

`--set calibration` compares three decision rules over a grid and prints/saves the stats:
  (a) minimum:   answer when the best sentence's similarity >= t;
  (b) relative:  answer when best minus the mean of the other candidates >= t;
  (c) combined:  (a) AND the best sentence's chunk was also found by the full-text branch.
An answerable question counts as right only if a quoted sentence contains one of its
`answer_has_any` strings; an unanswerable one only if it is refused.

`--set eval` (held-out, run once after choosing): asks every question through the real
answer service (`answer_question` with the FakeLLM and the thresholds of
`app/core/similarity.py`) and grades the response with `judge`.

The PDFs are not in git (see sample_data/calibration/README.md). Run in the backend image
(it has the embedding model) on the Compose network, e.g.:

    docker run --rm --cpus 3 -e OMP_NUM_THREADS=2 -e MKL_NUM_THREADS=2 \
      --network rutaviva_default -e JWT_SECRET=calibration-only-0123456789abcdef \
      -v ./backend/app:/app/app:ro -v ./backend/scripts:/app/scripts:ro \
      -v ./sample_data:/sample_data:ro -v ./var:/work \
      rutaviva-api python scripts/calibrate_extractive.py --set calibration

The saved rows hold similarities and flags only (no document text), so `--rows` can
re-run the analysis without the model or the database.
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.db.models import Chunk, Document, DocumentStatus, Tenant  # noqa: E402
from app.db.session import session_factory_for, tenant_transaction  # noqa: E402
from app.ingestion.chunking import chunk_document  # noqa: E402
from app.ingestion.pipeline import title_from_filename  # noqa: E402
from app.ingestion.types import ChunkDraft  # noqa: E402
from app.providers.llm.base import ContextChunk  # noqa: E402
from app.providers.llm.fake import MAX_SENTENCES, _candidates  # noqa: E402

APP_DB = "postgresql+psycopg://rutaviva_app:rutaviva_app@db:5432/rutaviva"
OWNER_DB = "postgresql+psycopg://rutaviva:rutaviva@db:5432/rutaviva"
QUESTIONS = {"calibration": "questions_calibration.yaml", "eval": "questions_eval_real.yaml"}


# --- Judging --------------------------------------------------------------------------------

_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def norm(text: str) -> str:
    """Case, whitespace and typographic quotes/dashes ignored (as the question verifier),
    because extracted text sometimes splits words or keeps curly quotes."""
    return re.sub(r"\s+", "", text.translate(_TRANSLATE).casefold())


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reason: str
    cited_expected_page: bool | None  # None: the question has no expected source
    retrieved_expected_page: bool | None


def judge(question: dict, response: dict) -> Verdict:
    """Grade one `QueryResponse` (as JSON) against the question's `expect`."""
    expect = question["expect"]
    sources = {(s["document"], s["page"]) for s in expect.get("sources", [])}
    citations = response["citations"]
    retrieved = (response.get("details") or {}).get("retrieved", [])
    cited = {(c["document_filename"], c["page"]) for c in citations}
    found = {(c["document_filename"], c["page"]) for c in retrieved}
    cited_page = bool(cited & sources) if sources else None
    retrieved_page = bool(found & sources) if sources else None

    answered = response["status"] == "answered"
    if expect["outcome"] == "insufficient_evidence":
        if not answered:
            return Verdict(True, "not enough information, as expected", None, None)
        where = ", ".join(f"{d} p.{p}" for d, p in sorted(cited))
        return Verdict(False, f"answered a question with no answer (quoted {where})", None, None)

    if not answered:
        why = "the expected page was not retrieved" if not retrieved_page else "page retrieved"
        return Verdict(False, f"said not enough information ({why})", cited_page, retrieved_page)
    quotes = norm(" ".join(c["quote"] for c in citations))
    if not any(norm(n) in quotes for n in expect["answer_has_any"]):
        where = ", ".join(f"{d} p.{p}" for d, p in sorted(cited))
        return Verdict(
            False,
            f"answered, but no quote has the expected text (quoted {where})",
            cited_page,
            retrieved_page,
        )
    return Verdict(True, "answered with the expected text", cited_page, retrieved_page)


# --- Corpus -------------------------------------------------------------------------------


def load_chunks(pdf: Path, sha256: str, cache: Path, settings: Settings) -> list[ChunkDraft]:
    """Chunks of one PDF, as the worker makes them; cached by file hash."""
    cached = cache / f"{pdf.stem}.{sha256[:12]}.chunks.json"
    if cached.exists():
        return [ChunkDraft(**{**d, "bbox": None}) for d in json.loads(cached.read_text())]
    from app.ingestion.pdfplumber_extract import PdfPlumberExtractor

    drafts = chunk_document(
        PdfPlumberExtractor().extract(pdf.read_bytes()),
        title=title_from_filename(pdf.name),
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps([{**asdict(d), "bbox": None} for d in drafts]))
    return drafts


class CachedEmbeddings:
    """The real provider with a disk cache keyed by the text's hash, so a rerun (and the
    fake LLM, which embeds the same sentences again) only embeds what is new. On a
    shared 4-CPU machine BGE takes seconds per chunk, so this saves most of a rerun."""

    def __init__(self, provider, path: Path) -> None:
        self.provider = provider
        self.model_id = provider.model_id
        self.dim = provider.dim
        self.tokens_estimated = provider.tokens_estimated
        self.path = path
        self.vectors: dict[str, list[float]] = json.loads(path.read_text()) if path.exists() else {}

    def _cached(self, texts, embed, prefix: str) -> list[list[float]]:
        keys = [hashlib.sha256((prefix + t).encode()).hexdigest() for t in texts]
        missing = sorted(
            {k: t for k, t in zip(keys, texts, strict=True) if k not in self.vectors}.items()
        )
        if missing:
            new = embed([t for _, t in missing])
            self.vectors.update({k: v for (k, _), v in zip(missing, new, strict=True)})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.vectors))
        return [self.vectors[k] for k in keys]

    def embed_documents(self, texts) -> list[list[float]]:
        return self._cached(list(texts), self.provider.embed_documents, "doc:")

    def embed_query(self, text: str) -> list[float]:
        return self._cached([text], lambda ts: [self.provider.embed_query(ts[0])], "query:")[0]

    def count_tokens(self, texts) -> int:
        return self.provider.count_tokens(texts)


@contextmanager
def throwaway_tenant(owner_url: str) -> Iterator[uuid.UUID]:
    """A tenant that exists only for this run; deleted with its documents at the end."""
    factory = session_factory_for(owner_url)
    tenant_id = uuid.uuid4()
    with factory() as session, session.begin():
        session.add(Tenant(id=tenant_id, name=f"calibration_{tenant_id.hex[:8]}"))
    try:
        yield tenant_id
    finally:
        with tenant_transaction(factory, tenant_id) as session:
            session.execute(delete(Document).where(Document.tenant_id == tenant_id))
        with factory() as session, session.begin():
            session.execute(delete(Tenant).where(Tenant.id == tenant_id))


def index_documents(owner_url, tenant_id, contracts, pdfs, cache, embeddings, settings) -> None:
    factory = session_factory_for(owner_url)
    for contract in contracts:
        drafts = load_chunks(pdfs / contract["file"], contract["sha256"], cache, settings)
        vectors = embeddings.embed_documents([d.embed_text for d in drafts])
        with tenant_transaction(factory, tenant_id) as session:
            document = Document(
                tenant_id=tenant_id,
                filename=contract["file"],
                sha256=contract["sha256"],
                status=DocumentStatus.READY,
                page_count=contract["pages"],
                chunk_count=len(drafts),
                embedding_model=embeddings.model_id,
            )
            session.add(document)
            session.flush()
            for draft, vector in zip(drafts, vectors, strict=True):
                session.add(
                    Chunk(
                        document_id=document.id,
                        tenant_id=tenant_id,
                        page=draft.page,
                        chunk_index=draft.chunk_index,
                        content=draft.content,
                        embedding=vector,
                        page_width=draft.page_width,
                        page_height=draft.page_height,
                    )
                )
        print(f"  {contract['file']}: {len(drafts)} chunks", flush=True)


# --- Scoring ------------------------------------------------------------------------------


def score_question(question, session, tenant_id, embeddings, settings) -> dict:
    """What the fake LLM sees for one question: every candidate sentence, best first,
    with its similarity, source and flags. No text is kept."""
    from app.retrieval.hybrid import search

    query = embeddings.embed_query(question["question"])
    retrieved = search(
        session,
        tenant_id=tenant_id,
        query_vector=query,
        question=question["question"],
        embedding_model=embeddings.model_id,
        settings=settings,
    )
    context = [
        ContextChunk(c.chunk_id, c.document_id, c.filename, c.page, c.content, c.flagged_injection)
        for c in retrieved
    ]
    fts = {c.chunk_id: c.fts_rank is not None for c in retrieved}
    candidates = _candidates(context)
    vectors = embeddings.embed_documents([c[3] for c in candidates])
    needles = [norm(n) for n in question["expect"].get("answer_has_any", [])]
    sentences = [
        {
            "sim": sum(a * b for a, b in zip(query, vector, strict=True)),
            "document": chunk.filename,
            "page": chunk.page,
            "has_answer": any(n in norm(text) for n in needles),
            "chunk_fts": fts[chunk.chunk_id],
        }
        for vector, (_, _, chunk, text) in zip(vectors, candidates, strict=True)
    ]
    sentences.sort(key=lambda s: -s["sim"])  # stable: ties keep the retrieval order
    return {
        "id": question["id"],
        "kind": question["kind"],
        "answerable": question["expect"]["outcome"] == "answered",
        "sources": [[s["document"], s["page"]] for s in question["expect"].get("sources", [])],
        "retrieved": [[c.filename, c.page, c.vec_rank, c.fts_rank] for c in retrieved],
        "sentences": sentences,
    }


# --- Analysis (calibration) ---------------------------------------------------------------


def signal(rule: str, row: dict) -> float:
    sims = [s["sim"] for s in row["sentences"]]
    if not sims:
        return float("-inf")
    if rule == "relative":
        rest = sims[1:]
        return sims[0] - (statistics.fmean(rest) if rest else 0.0)
    if rule == "combined" and not row["sentences"][0]["chunk_fts"]:
        return float("-inf")
    return sims[0]


def is_right(rule: str, row: dict, threshold: float, margin: float) -> bool:
    answered = signal(rule, row) >= threshold
    if not row["answerable"]:
        return not answered
    if not answered:
        return False
    top = row["sentences"][0]["sim"]
    # Same selection as the fake LLM: within `margin` of the best, at most MAX_SENTENCES.
    floor = top - margin if rule == "relative" else max(top - margin, threshold)
    kept = [s for s in row["sentences"] if s["sim"] >= floor][:MAX_SENTENCES]
    return any(s["has_answer"] for s in kept)


def describe(values: list[float]) -> dict:
    finite = sorted(v for v in values if v != float("-inf"))
    if not finite:
        return {"n": len(values)}
    return {
        "n": len(values),
        "no_signal": len(values) - len(finite),
        "min": round(finite[0], 4),
        "median": round(statistics.median(finite), 4),
        "max": round(finite[-1], 4),
    }


def auc(positives: list[float], negatives: list[float]) -> float:
    """Probability that a random answerable question scores above a random unanswerable
    one (ties count half): 1.0 = perfectly separable, 0.5 = no better than chance."""
    pairs = [(p > n) + 0.5 * (p == n) for p in positives for n in negatives]
    return round(sum(pairs) / len(pairs), 3)


def analyse(rows: list[dict]) -> dict:
    grids = {
        "minimum": [x / 200 for x in range(100, 200)],  # 0.500 .. 0.995
        "relative": [x / 200 for x in range(0, 80)],  # 0.000 .. 0.395
        "combined": [x / 200 for x in range(100, 200)],
    }
    margins = [x / 100 for x in range(0, 11)]
    positives = [r for r in rows if r["answerable"]]
    negatives = [r for r in rows if not r["answerable"]]
    report: dict = {
        "n_questions": len(rows),
        "n_answerable": len(positives),
        "n_unanswerable": len(negatives),
        "answer_in_candidates": sum(
            any(s["has_answer"] for s in r["sentences"]) for r in positives
        ),
        "answer_page_retrieved": sum(
            any([d, p] in r["sources"] for d, p, *_ in r["retrieved"]) for r in positives
        ),
        "best_chunk_found_by_fts": {
            "answerable": sum(
                bool(r["sentences"]) and r["sentences"][0]["chunk_fts"] for r in positives
            ),
            "unanswerable": sum(
                bool(r["sentences"]) and r["sentences"][0]["chunk_fts"] for r in negatives
            ),
        },
        "rules": {},
    }
    for rule, grid in grids.items():
        pos = [signal(rule, r) for r in positives]
        neg = [signal(rule, r) for r in negatives]
        finite_pos = [v for v in pos if v != float("-inf")]
        finite_neg = [v for v in neg if v != float("-inf")] or [float("-inf")]
        # Best score over (threshold, margin). Ties go to the middle tied value of each:
        # the one least sensitive to a small change in the data.
        scores = {m: [sum(is_right(rule, r, t, m) for r in rows) for t in grid] for m in margins}
        right = max(max(s) for s in scores.values())
        tied = [m for m in margins if max(scores[m]) == right]
        margin = tied[len(tied) // 2]
        plateau = [t for t, s in zip(grid, scores[margin], strict=True) if s == right]
        threshold = plateau[len(plateau) // 2]
        right_pos = sum(is_right(rule, r, threshold, margin) for r in positives)
        right_neg = sum(is_right(rule, r, threshold, margin) for r in negatives)
        report["rules"][rule] = {
            "best_accuracy": round(right / len(rows), 3),
            "threshold": threshold,
            "plateau": [plateau[0], plateau[-1]],
            "margin": margin,
            "tied_margins": [tied[0], tied[-1]],
            "answerable_right": right_pos,
            "unanswerable_right": right_neg,
            "answerable_signal": describe(pos),
            "unanswerable_signal": describe(neg),
            # > 0: a clean gap between the classes; < 0: they overlap by that much.
            "gap": round(min(finite_pos) - max(finite_neg), 4) if finite_pos else None,
            "unanswerable_above_lowest_answerable": sum(v >= min(finite_pos) for v in neg),
            "auc": auc(pos, neg),
        }
    return report


def question_summary(rows: list[dict]) -> list[dict]:
    """One line per question, ids and numbers only (no document text)."""
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "answerable": r["answerable"],
            "best_similarity": round(r["sentences"][0]["sim"], 4) if r["sentences"] else None,
            "answer_rank": next((i for i, s in enumerate(r["sentences"]) if s["has_answer"]), None),
            "best_chunk_found_by_fts": r["sentences"][0]["chunk_fts"] if r["sentences"] else None,
            "answer_page_retrieved": any([d, p] in r["sources"] for d, p, *_ in r["retrieved"]),
        }
        for r in rows
    ]


def print_rows(rows: list[dict]) -> None:
    print("\nid | kind | top-3 similarities | first answering sentence (rank) | best chunk fts")
    for r in rows:
        sims = ", ".join(f"{s['sim']:.3f}" for s in r["sentences"][:3])
        hit = next((i for i, s in enumerate(r["sentences"]) if s["has_answer"]), None)
        fts = r["sentences"][0]["chunk_fts"] if r["sentences"] else None
        print(f"{r['id']} | {r['kind']} | {sims} | {hit} | {fts}")


# --- Held-out evaluation ------------------------------------------------------------------


def evaluate(questions, session_for, tenant_id, embeddings, settings) -> dict:
    from app.generation.service import answer_question
    from app.providers.llm.fake import FakeLLM

    llm = FakeLLM(embeddings)
    results = []
    for q in questions:
        with session_for() as session:
            row = score_question(q, session, tenant_id, embeddings, settings)
            response = answer_question(
                q["question"],
                tenant_id=tenant_id,
                session=session,
                embeddings=embeddings,
                llm=llm,
                settings=settings,
            ).model_dump(mode="json")
        verdict = judge(q, response)
        top = row["sentences"][0]["sim"] if row["sentences"] else None
        results.append(
            {
                "id": q["id"],
                "kind": q["kind"],
                "answerable": row["answerable"],
                "passed": verdict.passed,
                "reason": verdict.reason,
                "cited_expected_page": verdict.cited_expected_page,
                "retrieved_expected_page": verdict.retrieved_expected_page,
                "best_similarity": round(top, 4) if top is not None else None,
                "answer_in_candidates": any(s["has_answer"] for s in row["sentences"]),
            }
        )
        mark = "PASS" if verdict.passed else "FAIL"
        print(f"{mark} {q['id']} ({q['kind']}): {verdict.reason} [best {top:.3f}]", flush=True)
    return {"thresholds": asdict(llm.thresholds), "questions": results}


# --- Main ---------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=sorted(QUESTIONS), required=True)
    parser.add_argument("--data", type=Path, default=Path("/sample_data/calibration"))
    parser.add_argument(
        "--pdfs",
        type=Path,
        default=Path(os.environ.get("CALIBRATION_PDFS", "/work/calibration-contracts")),
    )
    parser.add_argument("--cache", type=Path, default=Path("/work/calibration-cache"))
    parser.add_argument("--out", type=Path, help="JSON file for the rows and the report")
    parser.add_argument("--rows", type=Path, help="analyse saved rows instead of recomputing")
    parser.add_argument("--results", type=Path, help="with --rows: write the summary to commit")
    parser.add_argument("--chosen", choices=["minimum", "relative", "combined"], default="minimum")
    parser.add_argument(
        "--extract-only", action="store_true", help="only fill the chunk cache (no model, no DB)"
    )
    parser.add_argument("--database-url", default=APP_DB)
    parser.add_argument("--owner-database-url", default=OWNER_DB)
    args = parser.parse_args()

    if args.rows:
        saved = json.loads(args.rows.read_text())
        print_rows(saved["rows"])
        report = analyse(saved["rows"])
        print(json.dumps(report, indent=2))
        if args.results:
            results = {
                "embedding_model": saved["embedding_model"],
                "questions_file": QUESTIONS["calibration"],
                "chosen_rule": args.chosen,
                "chosen_values": {
                    k: report["rules"][args.chosen][k] for k in ("threshold", "margin")
                },
                "report": report,
                "questions": question_summary(saved["rows"]),
            }
            args.results.write_text(json.dumps(results, indent=2) + "\n")
            print(f"saved {args.results}")
        return

    manifest = yaml.safe_load((args.data / "contracts.yaml").read_text())["contracts"]
    contracts = [c for c in manifest if c["set"] == args.set]
    for c in contracts:
        digest = hashlib.sha256((args.pdfs / c["file"]).read_bytes()).hexdigest()
        if digest != c["sha256"]:
            sys.exit(f"{c['file']}: sha256 does not match the manifest")
    questions = yaml.safe_load((args.data / QUESTIONS[args.set]).read_text())["questions"]

    # `model_construct` skips validation: Settings refuses LLM_PROVIDER=fake for a model
    # with no calibrated thresholds, and producing them is the point of this script.
    settings = Settings.model_construct(embeddings_provider="local")
    if args.extract_only:
        for c in contracts:
            drafts = load_chunks(args.pdfs / c["file"], c["sha256"], args.cache, settings)
            print(f"  {c['file']}: {len(drafts)} chunks")
        return
    from app.providers import get_embeddings

    embeddings = CachedEmbeddings(
        get_embeddings(settings), args.cache / f"embeddings.{settings.embedding_model_id}.json"
    )
    app_factory = session_factory_for(args.database_url)

    with throwaway_tenant(args.owner_database_url) as tenant_id:
        print(f"indexing {len(contracts)} {args.set} contracts ({embeddings.model_id})")
        index_documents(
            args.owner_database_url,
            tenant_id,
            contracts,
            args.pdfs,
            args.cache,
            embeddings,
            settings,
        )

        def session_for():
            return tenant_transaction(app_factory, tenant_id)

        if args.set == "eval":
            output = evaluate(questions, session_for, tenant_id, embeddings, settings)
        else:
            rows = []
            for q in questions:
                with session_for() as session:
                    rows.append(score_question(q, session, tenant_id, embeddings, settings))
            print_rows(rows)
            output = {"rows": rows, "report": analyse(rows)}
            print(json.dumps(output["report"], indent=2))

    output["embedding_model"] = embeddings.model_id
    if args.out:
        args.out.write_text(json.dumps(output, indent=2))
        print(f"saved {args.out}")


if __name__ == "__main__":
    main()
