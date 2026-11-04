"""Checks on untrusted content: injected instructions and sources that disagree (ADR-013).

1. `detect_injection(text)`: does a chunk look like instructions addressed to the
   assistant? Run once at indexing; a flagged chunk is stored (the rest of the document
   is legitimate data) but never sent to the LLM, and the answer carries a warning.
   It is a short list of named patterns in English and Spanish, matched on text with
   case, accents and whitespace runs normalised. Pattern-based, so it is bypassable
   (paraphrases, other languages, text split across chunks): it is one layer. The others
   are the prompt (document content is delimited and declared DATA) and the validator
   (every citation must quote a chunk that was actually given to the model).
   Patterns are written to need the "addressed to the assistant" part: "ignore all
   previous instructions" matches, "Supplier shall follow Rutaviva's written
   instructions" does not; "you must now act as" matches, "Supplier shall act as an
   independent contractor" does not.

2. `sources_disagree(citations)`: do the quotes of an answer, taken from at least two
   different documents, state different numbers for the same kind of quantity? Three
   kinds are compared: percentages ("1.5%", "2 percent"), durations in hours or days
   ("72 business hours", "(48) hours") and money ("$0.02", "USD 5.00"). For each kind,
   if two documents both mention it and their sets of values differ, the sources
   disagree. Only the quotes are compared (they are the evidence the answer relies on),
   not the whole chunks. Limits: it does not know what the numbers refer to (two
   contracts with different penalties on different topics also "disagree", which only
   costs confidence), and it misses contradictions in words ("monthly" vs "weekly").
"""

import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Sequence

from app.schemas.query import Citation


def normalise_for_matching(text: str) -> str:
    """Casefold, strip accents (á -> a) and collapse whitespace runs into one space."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.split()).casefold()


# --- 1. Injected instructions -------------------------------------------------------------

# (name, pattern) over normalised text. The name is what tests and logs refer to.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+)*"
            r"(?:previous|prior|above|earlier|preceding|your|system)\s+"
            r"(?:instructions|rules|prompts?|directions)\b"
        ),
    ),
    ("you_are_now", re.compile(r"\byou are now\b")),
    # Bare "act as" is ordinary contract language ("act as an independent contractor"):
    # only "you (must/will/should) (now) act as" is an instruction to the reader.
    ("you_act_as", re.compile(r"\byou\s+(?:must\s+|will\s+|should\s+)?(?:now\s+)?act as\b")),
    # A role marker that starts the text or a sentence: "System: ...", "Assistant: ...".
    (
        "role_marker",
        re.compile(r"(?:^|[.!?;]\s|\]\s?)(?:system|assistant|developer|sistema|asistente)\s?:"),
    ),
    (
        "reveal_prompt",
        re.compile(
            r"\b(?:reveal|print|show|repeat|disclose|output)\s+"
            r"(?:your\s+(?:system\s+|hidden\s+)?(?:prompt|instructions)|the\s+(?:system\s+)?prompt)\b"
        ),
    ),
    (
        "hide_from_user",
        re.compile(
            r"\b(?:do not|don't|never)\s+(?:tell|inform|mention\s+(?:this\s+)?to|reveal\s+"
            r"(?:this\s+)?to)\s+the\s+user\b"
        ),
    ),
    ("respond_only_with", re.compile(r"\b(?:respond|reply|answer)\s+only\s+(?:with|that)\b")),
    (
        "when_asked_respond",
        re.compile(r"\bwhen asked\b.{0,80}?\b(?:respond|reply|answer|say)\s+that\b"),
    ),
    (
        "addressed_to_ai",
        re.compile(
            r"\b(?:notice|attention|message|note)\s+(?:to|for)\s+(?:all\s+)?"
            r"(?:ai|llm|automated|language model)\b"
        ),
    ),
    # Spanish. Imperatives in the second person (tú): "ignora/olvida las instrucciones";
    # the third person ("el Proveedor no ignore las instrucciones") is only flagged
    # together with "anteriores/previas".
    (
        "es_ignora_instrucciones",
        re.compile(
            r"\b(?:ignora|olvida|descarta|omite)\s+(?:todas\s+)?(?:las|tus)\s+"
            r"(?:instrucciones|reglas|indicaciones)\b"
            r"|\b(?:ignore|olvide|descarte|omita)\s+(?:todas\s+)?(?:las\s+)?"
            r"(?:instrucciones|reglas|indicaciones)\s+(?:anteriores|previas)\b"
        ),
    ),
    ("es_eres_ahora", re.compile(r"\b(?:eres ahora|ahora eres|ahora actua como)\b")),
    ("es_responde_solo", re.compile(r"\bresponde\s+(?:unicamente|solamente|solo)\b")),
    (
        "es_ocultar_al_usuario",
        re.compile(r"\bno\s+(?:le\s+)?(?:digas|menciones|cuentes|reveles)\b.{0,30}?\bal usuario\b"),
    ),
    (
        "es_revela_prompt",
        re.compile(
            r"\b(?:revela|muestra|imprime|repite)\s+(?:tu|tus|el)\s+"
            r"(?:prompt|instrucciones(?:\s+del\s+sistema)?)\b"
        ),
    ),
]


def matched_pattern(text: str) -> str | None:
    """Name of the first injection pattern the text matches, or None."""
    normalised = normalise_for_matching(text)
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(normalised):
            return name
    return None


def detect_injection(text: str) -> bool:
    """True when the text looks like instructions addressed to the assistant."""
    return matched_pattern(text) is not None


# --- 2. Sources that disagree -------------------------------------------------------------

_NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
# "(72) business hours": the closing parenthesis of the spelled-out form is allowed.
_QUANTITIES: dict[str, re.Pattern[str]] = {
    "percent": re.compile(_NUMBER + r"\s*(?:%|percent\b)"),
    "duration": re.compile(
        _NUMBER + r"\)?\s*(?:business\s+|calendar\s+|working\s+)?(hour|day)s?\b"
    ),
    "money": re.compile(r"(?:\$|\busd\s*)" + _NUMBER),
}


def _values(kind: str, quote: str) -> set[str]:
    """The values of one kind of quantity in a quote, as comparable strings
    ("1.50%" and "1.5%" both become "1.5"; durations keep their unit: "72 hour")."""
    found: set[str] = set()
    for match in _QUANTITIES[kind].finditer(normalise_for_matching(quote)):
        number = f"{float(match.group(1).replace(',', '')):g}"
        found.add(f"{number} {match.group(2)}" if kind == "duration" else number)
    return found


def sources_disagree(citations: Sequence[Citation]) -> bool:
    """True when quotes from two different documents give different values for the
    same kind of quantity (see the module docstring)."""
    quotes_by_document: dict[uuid.UUID, list[str]] = defaultdict(list)
    for citation in citations:
        quotes_by_document[citation.document_id].append(citation.quote)
    if len(quotes_by_document) < 2:
        return False

    for kind in _QUANTITIES:
        value_sets = [
            values
            for quotes in quotes_by_document.values()
            if (values := set().union(*(_values(kind, quote) for quote in quotes)))
        ]
        if len(value_sets) >= 2 and any(values != value_sets[0] for values in value_sets[1:]):
            return True
    return False
