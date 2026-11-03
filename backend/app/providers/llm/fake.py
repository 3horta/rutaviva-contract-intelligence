"""Deterministic, extractive fake LLM: the default, so the whole pipeline runs with no key.

It does not "understand" anything. It picks the 1-2 sentences of the context that share
the most content words with the question and returns them, verbatim, as the answer and
as the citation quotes. That is enough to exercise everything around a real model:
JSON output, citations that the validator checks against the chunks, "insufficient
evidence" when nothing matches, and failure modes (`settings.fake_llm_mode`) to test
retries, timeouts and invalid output without a network.
"""

import json
import math
import re
import time
import unicodedata

from app.core.config import FakeLLMMode
from app.core.errors import ProviderTimeoutError
from app.providers.llm.base import LLMResult, PromptInput

FAKE_LLM_MODEL = "fake-extractive-v1"

# A sentence must share at least this many content words with the question (or all of
# them, if the question has fewer) to count as supporting evidence.
MIN_OVERLAP = 2
# Sharing at least this fraction of the question's content words is a "strong" match.
STRONG_OVERLAP = 0.6
MAX_SENTENCES = 2

_WORD = re.compile(r"[a-z0-9]+")
# Sentence boundary: end punctuation followed by whitespace, or a line break.
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\n+")
_STOPWORDS = frozenset(
    {
        "a", "about", "after", "all", "an", "and", "any", "apply", "applies", "are", "as",
        "at", "be", "been", "being", "by", "can", "could", "do", "does", "each", "for",
        "from", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its",
        "many", "may", "me", "much", "must", "not", "of", "on", "or", "other", "our",
        "shall", "should", "so", "such", "than", "that", "the", "their", "them", "then",
        "there", "these", "they", "this", "those", "to", "under", "upon", "us", "was",
        "we", "were", "what", "when", "where", "which", "while", "who", "why", "will",
        "with", "within", "would", "you", "your",
    }
)  # fmt: skip

_NOT_JSON = "Sorry, I cannot produce JSON right now."


def _stem(word: str) -> str:
    """Very simple plural stripping: "penalties" -> "penalty", "tires" -> "tire"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def content_words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return {_stem(w) for w in _WORD.findall(text) if w not in _STOPWORDS}


def split_sentences(text: str) -> list[str]:
    """Sentences as exact substrings of `text` (only surrounding whitespace removed), so
    a quote taken from here is always found verbatim in the chunk."""
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


class FakeLLM:
    def __init__(self, mode: FakeLLMMode = "ok") -> None:
        self.mode = mode
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
        question_words = content_words(prompt.question)
        if not question_words:
            return _insufficient()
        needed = min(MIN_OVERLAP, len(question_words))

        # (overlap, chunk position, sentence position, chunk, sentence). Ties keep the
        # retrieval order, so the output depends only on the input.
        scored = []
        for chunk_pos, chunk in enumerate(prompt.chunks):
            for sentence_pos, sentence in enumerate(split_sentences(chunk.text)):
                overlap = len(question_words & content_words(sentence))
                if overlap >= needed:
                    scored.append((overlap, chunk_pos, sentence_pos, chunk, sentence))
        if not scored:
            return _insufficient()

        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        best = scored[:MAX_SENTENCES]
        strong = best[0][0] / len(question_words) >= STRONG_OVERLAP
        confidence = "high" if len(best) >= 2 or strong else "medium"
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
