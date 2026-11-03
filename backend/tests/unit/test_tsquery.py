import pytest

from app.retrieval.tsquery import MAX_TERMS, build_or_tsquery


def test_question_becomes_or_of_content_words() -> None:
    assert build_or_tsquery("What is the penalty for late tire deliveries?") == (
        "penalty | late | tire | deliveries"
    )


@pytest.mark.parametrize(
    "question",
    [
        "penalty & late | !tire",
        "penalty:* & (late",
        "O'Brien's \"penalty\" (late)",
        "'; DROP TABLE chunks; --",
    ],
)
def test_tsquery_syntax_and_quotes_never_reach_the_query(question: str) -> None:
    query = build_or_tsquery(question)
    assert query is not None
    # Only plain words joined by " | ": nothing the tsquery parser could interpret.
    for term in query.split(" | "):
        assert term.isalnum() and term == term.lower()


def test_operators_are_removed_but_words_kept() -> None:
    assert build_or_tsquery("penalty & late | !tire:*") == "penalty | late | tire"


@pytest.mark.parametrize("question", ["", "   ", "&|!():'\"", "what is the"])
def test_no_usable_words_means_no_fts_branch(question: str) -> None:
    assert build_or_tsquery(question) is None


def test_accents_are_stripped_and_duplicates_dropped() -> None:
    assert build_or_tsquery("Pénalty PENALTY penalty") == "penalty"


def test_number_of_terms_is_capped() -> None:
    question = " ".join(f"word{i}" for i in range(100))
    query = build_or_tsquery(question)
    assert query is not None
    assert len(query.split(" | ")) == MAX_TERMS
