"""Build a safe full-text query from a natural-language question.

The user's question is never interpolated into SQL and never handed to `to_tsquery` as
is: its syntax (`&`, `|`, `!`, `:`, `'`, parentheses) would either raise a syntax error
or let the user shape the query. Instead the question is reduced to plain `[a-z0-9]`
words joined with `|` (OR), and that string is still passed as a bound parameter to
`to_tsquery('english', :tsquery)`, where Postgres applies stemming and its stopword list.

Why OR and not AND (`plainto_tsquery` / `websearch_to_tsquery`): a question has many
words that the answer does not repeat ("what", "must", "under"...). With AND a single
missing word drops the chunk; with OR, `ts_rank_cd` still ranks chunks that share more
words higher, and the vector branch covers the rest.
"""

import re
import unicodedata

_WORD = re.compile(r"[a-z0-9]+")

# Words that are common in questions and carry no meaning for matching. Postgres also
# drops English stopwords, but removing them here means a question made only of them
# gives no FTS branch at all instead of an empty tsquery (which Postgres warns about).
_STOPWORDS = frozenset(
    {
        "a", "about", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do",
        "does", "for", "from", "has", "have", "how", "i", "if", "in", "is", "it", "its",
        "me", "much", "of", "on", "or", "our", "should", "tell", "that", "the", "their",
        "there", "this", "to", "under", "was", "we", "what", "when", "where", "which",
        "who", "why", "will", "with", "would", "you",
    }
)  # fmt: skip

# Upper bound on terms, so a 2,000-character question cannot build a huge query.
MAX_TERMS = 32


def build_or_tsquery(question: str) -> str | None:
    """Return a `to_tsquery` input such as `"penalty | late | tire"`, or None when the
    question has no usable words (then the full-text branch is skipped)."""
    # Strip accents and lowercase, so "Pénalty" and "penalty" are the same word.
    text = unicodedata.normalize("NFKD", question)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()

    terms: list[str] = []
    for word in _WORD.findall(text):
        if word in _STOPWORDS or word in terms:
            continue
        terms.append(word)
        if len(terms) == MAX_TERMS:
            break
    if not terms:
        return None
    # Each term is only [a-z0-9], so it cannot contain tsquery operators.
    return " | ".join(terms)
