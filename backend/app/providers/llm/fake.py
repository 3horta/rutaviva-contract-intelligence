"""Deterministic, extractive fake LLM: the default, so the whole pipeline runs with no key.

It does not write anything. It ranks the sentences of the context by cosine similarity
to the question, using the SAME embeddings provider as retrieval, and quotes the best
ones verbatim, inside a short neutral template that names the document and page.

Which sentences (calibrated thresholds per embedding model, see `app/core/similarity.py`):
  1. sorted by similarity, s1 >= s2 >= ...; if s1 < `minimum`: insufficient evidence;
  2. every sentence with s >= s1 - `margin` is kept, up to `MAX_SENTENCES`. The cut is
     relative to the best one because a retrieval model compresses cosines (most
     sentences of the same domain score 0.6-0.9): 0.99 / 0.40 keeps one sentence,
     0.95 / 0.92 keeps both.
Confidence: it declares `high` whenever it answers (it only quotes verbatim, and a
similarity is not a probability of being right); the service takes the minimum of that
and its retrieval signal, so in practice the retrieval signal decides.

Never quoted: sentences of chunks flagged as prompt injection (an extractor cannot tell
an injected instruction from a clause), and text repeated by the overlap between chunks
(quoted once). The templates only add neutral connectors, never an interpretation.

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

import hashlib
import json
import math
import re
import time
import unicodedata

from app.core.config import FakeLLMMode
from app.core.errors import ProviderTimeoutError
from app.core.similarity import SimilarityThresholds, calibrated_thresholds
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import ContextChunk, LLMResult, PromptInput

FAKE_LLM_MODEL = "fake-extractive-v3"

MAX_SENTENCES = 3
# Fragments shorter than this are headings ("7.2 Late Delivery.", "Incident Reporting
# Protocol RV-IRP-7.") or list markers. Alone they look very much like the question and
# answer nothing, so each one is joined to the sentence that follows it.
HEADING_MAX_WORDS = 7
TRAILING_MIN_WORDS = 5


_WORD = re.compile(r"[a-z0-9]+")
# Sentence boundary: end punctuation followed by whitespace, or a line break. A period
# after a common abbreviation ("8:00 a.m. to 5:00 p.m.", "Inc.", "e.g.") is not a
# boundary, and neither is one followed by a lowercase word: a sentence never starts so.
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\n+")
_ABBREVIATIONS = frozenset(
    {"a.m.", "p.m.", "e.g.", "i.e.", "etc.", "inc.", "co.", "corp.", "ltd.", "no.", "nos.",
     "u.s.", "approx.", "sec.", "art.", "vs.", "mr.", "ms.", "dr.", "st."}
)  # fmt: skip


def _is_boundary(text: str, boundary: re.Match[str]) -> bool:
    if "\n" in boundary.group():
        return True
    following = text[boundary.end() : boundary.end() + 1]
    if following.islower():
        return False
    last_word = text[: boundary.start()].rsplit(None, 1)[-1].lower() if boundary.start() else ""
    return last_word not in _ABBREVIATIONS


_NOT_JSON = "Sorry, I cannot produce JSON right now."

# The first sentence names its source; each follow-up names it relative to the sentence
# before it, so "it" always refers to the document just mentioned.
_FIRST = (
    'According to {filename} (page {page}): "{quote}"',
    '{filename} states on page {page}: "{quote}"',
    'The relevant clause, in {filename} (page {page}), reads: "{quote}"',
)
_SAME_PAGE = ' It also states: "{quote}"'
_SAME_DOCUMENT = ' On page {page} it also states: "{quote}"'
_OTHER_DOCUMENT = ' In addition, {filename} (page {page}) says: "{quote}"'


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
        if not _is_boundary(text, boundary):
            continue
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
        found = thresholds or calibrated_thresholds(embeddings.model_id)
        if found is None:
            # Settings already refuses to start in this case; this guards direct use.
            raise ValueError(f"no calibrated similarity thresholds for {embeddings.model_id}")
        self.thresholds = found
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

        candidates = _candidates(prompt.chunks)
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
        # Ties keep the retrieval order, so the output depends only on the input.
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        top = scored[0][0]
        if top < self.thresholds.minimum:
            return _insufficient()
        cutoff = max(top - self.thresholds.margin, self.thresholds.minimum)
        best = [item for item in scored if item[0] >= cutoff][:MAX_SENTENCES]
        return {
            "answer": _compose(
                prompt.question, [(chunk, sentence) for *_, chunk, sentence in best]
            ),
            "citations": [
                {"chunk_id": str(chunk.chunk_id), "quote": sentence} for *_, chunk, sentence in best
            ],
            "confidence": "high",
            "insufficient_evidence": False,
        }


def _candidates(chunks: list[ContextChunk]) -> list[tuple[int, int, ContextChunk, str]]:
    """(chunk position, sentence position, chunk, sentence) of every quotable sentence."""
    found = [
        (chunk_pos, sentence_pos, chunk, sentence)
        for chunk_pos, chunk in enumerate(chunks)
        if not chunk.flagged_injection
        for sentence_pos, sentence in enumerate(split_sentences(chunk.text))
    ]
    # The tail of a chunk is repeated at the start of the next one (overlap), so the
    # same sentence, or the end of one, can appear twice. Drop every candidate whose
    # text is inside another candidate; of exact duplicates keep the first.
    # Same normalisation as the validator (case and whitespace runs ignored).
    keys = [" ".join(item[3].split()).casefold() for item in found]
    return [
        item
        for i, item in enumerate(found)
        if not any(
            j != i and keys[i] in keys[j] and (keys[i] != keys[j] or j < i)
            for j in range(len(found))
        )
    ]


def _compose(question: str, quotes: list[tuple[ContextChunk, str]]) -> str:
    first, text = quotes[0]
    # Same question, same template, on every run (unlike `hash()`, which is salted).
    template = _FIRST[hashlib.sha256(question.encode()).digest()[0] % len(_FIRST)]
    parts = [template.format(filename=first.filename, page=first.page, quote=text)]
    previous = first
    for chunk, text in quotes[1:]:
        if chunk.document_id != previous.document_id:
            follow = _OTHER_DOCUMENT
        elif chunk.page != previous.page:
            follow = _SAME_DOCUMENT
        else:
            follow = _SAME_PAGE
        parts.append(follow.format(filename=chunk.filename, page=chunk.page, quote=text))
        previous = chunk
    return "".join(parts)


def _insufficient() -> dict:
    return {
        "answer": "",
        "citations": [],
        "confidence": "low",
        "insufficient_evidence": True,
    }
