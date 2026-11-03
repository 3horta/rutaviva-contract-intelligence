"""Deterministic, extractive fake LLM: the default, so the whole pipeline runs with no key.

It does not write anything. It ranks the sentences of the context by cosine similarity
to the question, using the SAME embeddings provider as retrieval, and returns the best
1-2 sentences verbatim as the answer and as the citation quotes.

- At runtime the embedder is the real local model (BGE), so a question matches a
  sentence by meaning: "truck arrives after the deadline" finds "fails to deliver within
  the agreed delivery window".
- In tests the embedder is the simulated one (hashing of words and trigrams), so the
  same code stays deterministic, offline and without PyTorch; matching is then lexical.

That is enough to exercise everything around a real model: JSON output, citations that
the validator checks against the chunks, "insufficient evidence" when nothing is close
enough, and failure modes (`settings.fake_llm_mode`) to test retries, timeouts and
invalid output without a network. An extractive QA model would point at the exact span
instead of a whole sentence; it is the next step if the evaluation set shows sentences
that look like the question without answering it (see the decisions log).
"""

import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass

from app.core.config import FakeLLMMode
from app.core.errors import ProviderTimeoutError
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import LLMResult, PromptInput

FAKE_LLM_MODEL = "fake-extractive-v2"

MAX_SENTENCES = 2
# Fragments shorter than this are headings ("7.2 Late Delivery.", "Incident Reporting
# Protocol RV-IRP-7.") or list markers. Alone they look very much like the question and
# answer nothing, so each one is joined to the sentence that follows it.
HEADING_MAX_WORDS = 7
TRAILING_MIN_WORDS = 5


@dataclass(frozen=True)
class SimilarityThresholds:
    # Below `minimum` a sentence is not evidence; the best one reaching `strong` gives
    # confidence "high".
    minimum: float
    strong: float


# Cosine scales are model-specific, so the thresholds are too. Keyed by embedding model
# name (the part of `model_id` before "@").
#   fake-hash-v1: measured; unrelated sentences score <= 0.19, answers 0.30-0.49.
#   BGE and any other real model: PROVISIONAL, to calibrate with the evaluation set.
THRESHOLDS: dict[str, SimilarityThresholds] = {
    "fake-hash-v1": SimilarityThresholds(minimum=0.25, strong=0.40),
}
DEFAULT_THRESHOLDS = SimilarityThresholds(minimum=0.55, strong=0.70)

_WORD = re.compile(r"[a-z0-9]+")
# Sentence boundary: end punctuation followed by whitespace, or a line break.
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\n+")

_NOT_JSON = "Sorry, I cannot produce JSON right now."


def thresholds_for(embedding_model_id: str) -> SimilarityThresholds:
    return THRESHOLDS.get(embedding_model_id.split("@", 1)[0], DEFAULT_THRESHOLDS)


def word_count(text: str) -> int:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return len(_WORD.findall(text))


def split_sentences(text: str) -> list[str]:
    """Sentences as exact substrings of `text`, so a quote taken from here is always
    found verbatim in the chunk. A heading is joined to the next sentence (keeping the
    original whitespace between them); a heading with nothing after it is dropped."""
    # (start, end) of each fragment between sentence boundaries.
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in _SENTENCE_END.finditer(text):
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(text)))

    sentences: list[str] = []
    pending_start: int | None = None  # start of headings waiting for their sentence
    for span_start, span_end in spans:
        fragment = text[span_start:span_end]
        if not fragment.strip():
            continue
        begin = span_start if pending_start is None else pending_start
        if word_count(fragment) <= HEADING_MAX_WORDS:
            pending_start = begin
            continue
        sentences.append(text[begin:span_end].strip())
        pending_start = None
    # Short text left at the end: a real short sentence ("Payment is due within 30
    # days.") is kept; a bare heading cut off by the chunk boundary is not.
    if pending_start is not None:
        tail = text[pending_start:].strip()
        if word_count(tail) >= TRAILING_MIN_WORDS:
            sentences.append(tail)
    return sentences


def _cosine(a: list[float], b: list[float]) -> float:
    # Both providers return L2-normalised vectors: the dot product is the cosine.
    return sum(x * y for x, y in zip(a, b, strict=True))


class FakeLLM:
    def __init__(
        self,
        embeddings: EmbeddingsProvider,
        mode: FakeLLMMode = "ok",
        thresholds: SimilarityThresholds | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.mode = mode
        self.thresholds = thresholds or thresholds_for(embeddings.model_id)
        self.model_id = FAKE_LLM_MODEL

    def generate(self, prompt: PromptInput, *, timeout_s: float) -> LLMResult:
        started = time.perf_counter()

        if self.mode == "timeout":
            raise ProviderTimeoutError(f"fake LLM timed out after {timeout_s}s")
        if self.mode == "empty":
            text = ""
        elif self.mode == "invalid" or (self.mode == "invalid_once" and prompt.attempt == 1):
            text = _NOT_JSON
        else:
            # Embedding errors are ProviderErrors and propagate as they are: the answer
            # service already retries transient ones and maps the rest to 503/504.
            text = json.dumps(self._answer(prompt))

        prompt_chars = len(prompt.question) + sum(len(c.text) for c in prompt.chunks)
        return LLMResult(
            text=text,
            model=self.model_id,
            # Rule of thumb: about 4 characters per token.
            input_tokens=math.ceil(prompt_chars / 4),
            output_tokens=math.ceil(len(text) / 4),
            tokens_estimated=True,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    def _answer(self, prompt: PromptInput) -> dict:
        if word_count(prompt.question) == 0:
            return _insufficient()

        # (chunk position, sentence position, chunk, sentence) of every candidate.
        candidates = [
            (chunk_pos, sentence_pos, chunk, sentence)
            for chunk_pos, chunk in enumerate(prompt.chunks)
            for sentence_pos, sentence in enumerate(split_sentences(chunk.text))
        ]
        if not candidates:
            return _insufficient()

        # One batch for all sentences. The question goes through `embed_query` (BGE adds
        # its retrieval instruction there), the sentences through `embed_documents`.
        question_vector = self.embeddings.embed_query(prompt.question)
        sentence_vectors = self.embeddings.embed_documents([c[3] for c in candidates])

        scored = [
            (_cosine(question_vector, vector), *candidate)
            for vector, candidate in zip(sentence_vectors, candidates, strict=True)
        ]
        scored = [item for item in scored if item[0] >= self.thresholds.minimum]
        if not scored:
            return _insufficient()

        # Ties keep the retrieval order, so the output depends only on the input.
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        best = scored[:MAX_SENTENCES]
        confidence = "high" if best[0][0] >= self.thresholds.strong else "medium"
        return {
            "answer": " ".join(sentence for *_, sentence in best),
            "citations": [
                {"chunk_id": str(chunk.chunk_id), "quote": sentence} for *_, chunk, sentence in best
            ],
            "confidence": confidence,
            "insufficient_evidence": False,
        }


def _insufficient() -> dict:
    return {
        "answer": "",
        "citations": [],
        "confidence": "low",
        "insufficient_evidence": True,
    }
