import pytest

from app.api.documents import content_disposition, sanitize_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("contract.pdf", "contract.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\me\\Contract.pdf", "Contract.pdf"),
        ("bad\r\nname\x00.pdf", "badname.pdf"),
        ("   ", "document.pdf"),
        (None, "document.pdf"),
        ("folder/", "document.pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_long_filename_is_cut_keeping_the_extension() -> None:
    name = sanitize_filename("a" * 500 + ".pdf")

    assert len(name) == 200
    assert name.endswith(".pdf")


def test_content_disposition_is_safe_for_quotes_and_unicode() -> None:
    header = content_disposition('Contrato "año"\\2026.pdf')

    assert header == (
        'inline; filename="Contrato _a_o__2026.pdf"; '
        "filename*=UTF-8''Contrato%20%22a%C3%B1o%22%5C2026.pdf"
    )
