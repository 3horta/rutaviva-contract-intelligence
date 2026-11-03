import pytest
from pydantic import ValidationError

from app.schemas.query import QueryRequest


@pytest.mark.parametrize("question", ["", "   ", "\n\t "])
def test_blank_question_is_rejected(question: str) -> None:
    with pytest.raises(ValidationError):
        QueryRequest(question=question)


def test_question_is_stripped() -> None:
    assert QueryRequest(question="  What is the detention rate?  ").question == (
        "What is the detention rate?"
    )
