"""Calibrate the BGE thresholds of the fake (extractive) LLM with the evaluation set.

Runs in the Docker image (needs Docling and the local embedding model), all in memory:
no database, no worker. For each tenant it extracts and chunks the sample PDFs as the
worker does, embeds the chunks, and for each question of sample_data/questions.yaml:
  1. retrieves the top chunks by cosine (vector branch only: an approximation of the
     hybrid search, enough to compare thresholds);
  2. scores every sentence of those chunks against the question;
  3. evaluates a grid of (minimum, margin) with the selection rule of the fake LLM: an
     answerable question is right when it is answered and one of `answer_has_any` is in
     a kept sentence; an unanswerable one is right when the best sentence is below the
     minimum.

    docker compose run --rm --no-deps \
      -v ./sample_data:/sample_data:ro api python scripts/calibrate_extractive.py
"""

import argparse
import os
import uuid
from pathlib import Path

import yaml

from app.core.config import Settings
from app.core.errors import IngestionError
from app.ingestion.chunking import chunk_document
from app.ingestion.extract import DoclingExtractor
from app.ingestion.pipeline import title_from_filename
from app.providers import get_embeddings
from app.providers.llm.base import ContextChunk
from app.providers.llm.fake import MAX_SENTENCES, _candidates


def _load_tenant(path: Path, extractor, embeddings, settings):
    chunks, vectors = [], []
    for pdf in sorted(path.glob("*.pdf")):
        try:
            doc = extractor.extract(pdf.read_bytes())
        except IngestionError as exc:  # corrupt / scanned samples are expected to fail
            print(f"  skip {pdf.name}: {type(exc).__name__}")
            continue
        drafts = chunk_document(
            doc,
            title=title_from_filename(pdf.name),
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )
        document_id = uuid.uuid4()
        vectors += embeddings.embed_documents([d.embed_text for d in drafts])
        chunks += [
            ContextChunk(uuid.uuid4(), document_id, pdf.name, d.page, d.content) for d in drafts
        ]
        print(f"  {pdf.name}: {len(drafts)} chunks")
    return chunks, vectors


def _retrieve(query, chunks, vectors, settings):
    scored = sorted(
        zip(chunks, vectors, strict=True),
        key=lambda cv: -sum(q * v for q, v in zip(query, cv[1], strict=True)),
    )
    kept, per_doc = [], {}
    for chunk, _ in scored:
        if per_doc.get(chunk.document_id, 0) >= settings.max_chunks_per_document:
            continue
        kept.append(chunk)
        per_doc[chunk.document_id] = per_doc.get(chunk.document_id, 0) + 1
        if len(kept) == settings.retrieval_k:
            break
    return kept


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("/sample_data"))
    args = parser.parse_args()

    # Without validation: Settings refuses LLM_PROVIDER=fake for a model with no
    # calibrated thresholds, and producing them is the point of this script.
    artifacts = os.environ.get("DOCLING_ARTIFACTS_PATH")
    settings = Settings.model_construct(
        embeddings_provider="local", docling_artifacts_path=Path(artifacts) if artifacts else None
    )
    embeddings = get_embeddings(settings)
    extractor = DoclingExtractor(settings.docling_artifacts_path)

    spec = yaml.safe_load((args.data / "questions.yaml").read_text())
    corpus = {}
    for tenant in sorted({q["tenant"] for q in spec["questions"]}):
        print(f"tenant {tenant}")
        corpus[tenant] = _load_tenant(args.data / "pdfs" / tenant, extractor, embeddings, settings)

    rows = []
    print("\nid | kind | expected | top similarities | rank of first sentence with the answer")
    for q in spec["questions"]:
        chunks, vectors = corpus[q["tenant"]]
        query = embeddings.embed_query(q["question"])
        context = _retrieve(query, chunks, vectors, settings)
        # Every quotable sentence, as the fake LLM sees them, with its similarity.
        sentences = [c[3] for c in _candidates(context)]
        scored = sorted(
            (
                (sum(a * b for a, b in zip(query, v, strict=True)), text)
                for v, text in zip(embeddings.embed_documents(sentences), sentences, strict=True)
            ),
            key=lambda p: -p[0],
        )
        sims = [sim for sim, _ in scored]
        quotes = [text for _, text in scored]
        expect = q["expect"]
        needles = [s.lower() for s in expect.get("answer_has_any", [])]
        hit = next(
            (i for i, quote in enumerate(quotes) if any(n in quote.lower() for n in needles)),
            None,
        )
        rows.append((q, expect["outcome"], sims, hit))
        print(
            f"{q['id']} | {q['kind']} | {expect['outcome']} | "
            f"{', '.join(f'{s:.3f}' for s in sims[:4])} | {hit}"
        )

    print("\nfloor margin | answerable ok | unanswerable ok | total ok | avg sentences")
    results = []
    for floor in [x / 100 for x in range(40, 91)]:
        for margin in [x / 100 for x in range(1, 9)]:
            ans_ok = unans_ok = sentences = answered = 0
            for _, outcome, sims, hit in rows:
                if not sims or sims[0] < floor:
                    unans_ok += outcome == "insufficient_evidence"
                    continue
                cutoff = max(sims[0] - margin, floor)
                kept = [i for i, s in enumerate(sims) if s >= cutoff][:MAX_SENTENCES]
                answered += 1
                sentences += len(kept)
                ans_ok += outcome == "answered" and hit is not None and hit in kept
            results.append(
                (ans_ok + unans_ok, ans_ok, unans_ok, floor, margin, sentences / max(answered, 1))
            )
    n_ans = sum(r[1] == "answered" for r in rows)
    print(f"(answerable: {n_ans}, unanswerable: {len(rows) - n_ans})")
    for total, a, u, floor, margin, avg in sorted(results, key=lambda r: (-r[0], -r[3], r[4]))[:15]:
        print(f"{floor:.2f} {margin:.2f} | {a} | {u} | {total} | {avg:.2f}")
    print(f"\nembedding model: {embeddings.model_id} (key in app/core/similarity.py)")


if __name__ == "__main__":
    main()
