"""The default extractor against the sample PDFs (pdfplumber runs on every platform)."""

from pathlib import Path

import pytest

from app.core.errors import CorruptPDFError, EmptyPDFError
from app.ingestion.pdfplumber_extract import PdfPlumberExtractor, is_heading

PDFS = Path(__file__).resolve().parents[3] / "sample_data" / "pdfs"


def _extract(relative: str):
    return PdfPlumberExtractor().extract((PDFS / relative).read_bytes())


@pytest.mark.parametrize(
    ("name", "pages"),
    [
        ("tenant_a/fleet_maintenance_agreement_rio_bravo.pdf", 4),
        ("tenant_a/tire_supply_agreement_frontera.pdf", 3),
        ("tenant_a/tire_supply_exhibit_b_sla_frontera.pdf", 1),
        ("tenant_a/fuel_supply_agreement_petroruta.pdf", 3),
        ("tenant_b/motor_carrier_agreement_halcon.pdf", 3),
        ("tenant_b/fleet_insurance_policy_centinela.pdf", 2),
    ],
)
def test_valid_contracts_have_their_pages_and_text(name: str, pages: int) -> None:
    doc = _extract(name)
    assert doc.page_count == pages
    assert all(page.text.strip() for page in doc.pages)


def test_known_clause_is_on_its_page_with_a_top_left_box() -> None:
    page = _extract("tenant_a/tire_supply_agreement_frontera.pdf").pages[1]
    [block] = [b for b in page.blocks if b.text.startswith("2.3 Late delivery penalty")]
    assert "one and a half percent (1.5%)" in block.text  # the clause is one block
    box = block.bbox
    assert box is not None
    assert 0 <= box.x0 < box.x1 <= page.width
    assert 0 <= box.top < box.bottom <= page.height


def test_running_headers_and_footers_are_dropped() -> None:
    doc = _extract("tenant_a/tire_supply_agreement_frontera.pdf")
    text = "\n".join(page.text for page in doc.pages)
    assert "Confidential" not in text
    assert "Page 1 of" not in text


def test_section_titles_are_headings() -> None:
    page = _extract("tenant_a/tire_supply_agreement_frontera.pdf").pages[1]
    headings = [b.text for b in page.blocks if b.kind == "heading"]
    assert "SECTION 2. ORDERS AND DELIVERY" in headings


def test_corrupted_pdf_raises_corrupt() -> None:
    with pytest.raises(CorruptPDFError):
        _extract("tenant_a/corrupted_contract.pdf")


def test_random_bytes_raise_corrupt() -> None:
    with pytest.raises(CorruptPDFError):
        PdfPlumberExtractor().extract(b"not a pdf at all")


def test_scanned_pdf_without_text_raises_empty() -> None:
    with pytest.raises(EmptyPDFError):
        _extract("tenant_b/scanned_lease_addendum_no_text.pdf")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SECTION 2. ORDERS AND DELIVERY", True),
        ("EXHIBIT B - SERVICE LEVELS", True),
        ("Exhibit C: Supplier special conditions", True),
        ("2.3 Late delivery penalty. If Supplier fails to deliver.", False),
        ("The Supplier shall deliver within 72 hours.", False),
    ],
)
def test_heading_rule(text: str, expected: bool) -> None:
    assert is_heading(text) is expected


def test_page_number_rule() -> None:
    from app.ingestion.pdfplumber_extract import _PAGE_NUMBER

    for text in ["Page 2 of 3", "page 4", "- 7 -", "12", "3/9"]:
        assert _PAGE_NUMBER.match(text)
    assert not _PAGE_NUMBER.match("4.2 Rutaviva may terminate with 30 days' notice.")


def test_a_non_repeated_line_in_the_margin_is_kept() -> None:
    from app.ingestion.pdfplumber_extract import _is_running, _running_lines

    def line(text: str, in_margin: bool = True) -> dict:
        return {"text": text, "in_margin": in_margin}

    raw = [
        (612.0, 792.0, [line("ACME Confidential"), line("Page 1 of 2")]),
        (
            612.0,
            792.0,
            [line("ACME Confidential"), line("9.4 Tires older than 6 years are rejected.")],
        ),
    ]
    running = _running_lines(raw)
    assert _is_running(line("ACME Confidential"), running)  # repeats: header
    assert _is_running(line("Page 1 of 2"), running)  # a page number
    assert not _is_running(line("9.4 Tires older than 6 years are rejected."), running)
    assert not _is_running(line("ACME Confidential", in_margin=False), running)  # in the body
