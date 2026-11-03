"""Docling extraction over the sample PDFs.

Docling is only installed on Linux (worker image and CI). Elsewhere these tests are
skipped; with REQUIRE_DOCLING=1 (set in CI) a missing Docling fails them instead, so a
green run means they actually ran.
"""

import importlib.util
import os
import time
from pathlib import Path

import pytest

from app.core.errors import CorruptPDFError, EmptyPDFError
from app.ingestion.extract import DoclingExtractor

SAMPLE_PDFS = Path(
    os.getenv("SAMPLE_PDFS_DIR", Path(__file__).resolve().parents[3] / "sample_data" / "pdfs")
)

# Page counts from sample_data/README.md.
VALID_PDFS = {
    "tenant_a/fleet_maintenance_agreement_rio_bravo.pdf": 4,
    "tenant_a/tire_supply_agreement_frontera.pdf": 3,
    "tenant_a/tire_supply_exhibit_b_sla_frontera.pdf": 1,
    "tenant_a/fuel_supply_agreement_petroruta.pdf": 3,
    "tenant_b/motor_carrier_agreement_halcon.pdf": 3,
    "tenant_b/fleet_insurance_policy_centinela.pdf": 2,
}


def _require_docling() -> None:
    if importlib.util.find_spec("docling") is not None:
        return
    reason = "Docling is not installed (Linux only: run in the worker container)"
    if os.getenv("REQUIRE_DOCLING") == "1":
        pytest.fail(f"REQUIRE_DOCLING=1 but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def extractor() -> DoclingExtractor:
    _require_docling()
    # One instance for the module: loading the models is the slow part.
    return DoclingExtractor(artifacts_path=None)


def _read(name: str) -> bytes:
    return (SAMPLE_PDFS / name).read_bytes()


def test_module_imports_without_docling():
    # The API and macOS tests import the module; Docling is only loaded on use.
    assert DoclingExtractor(artifacts_path=None)._converter is None


@pytest.mark.parametrize(("name", "pages"), VALID_PDFS.items())
def test_valid_contracts_have_their_pages_and_text(extractor, name, pages):
    started = time.perf_counter()
    document = extractor.extract(_read(name))
    elapsed = time.perf_counter() - started
    print(f"\n{name}: {elapsed:.2f} s for {document.page_count} pages")

    assert document.page_count == pages
    assert [page.number for page in document.pages] == list(range(1, pages + 1))
    assert all(page.text.strip() for page in document.pages)
    for page in document.pages:
        # US Letter, in PDF points.
        assert (round(page.width), round(page.height)) == (612, 792)
        for block in page.blocks:
            assert block.text.strip()
            assert block.bbox is not None
            # Top-left origin: top above bottom, everything inside the page.
            assert 0 <= block.bbox.x0 < block.bbox.x1 <= page.width + 1
            assert 0 <= block.bbox.top < block.bbox.bottom <= page.height + 1


def test_reading_order_and_top_left_origin(extractor):
    document = extractor.extract(_read("tenant_a/tire_supply_agreement_frontera.pdf"))
    first_page = document.pages[0]

    # The first block is the title, near the top of the page (bottom-left origin would
    # put it near the page height).
    assert first_page.blocks[0].bbox.top < first_page.height / 4
    tops = [b.bbox.top for b in first_page.blocks if b.kind not in ("page_header", "page_footer")]
    assert tops == sorted(tops)
    assert any(b.kind in ("heading", "title") for b in first_page.blocks)


def test_known_clause_is_on_its_page(extractor):
    # The README puts the 1.5% penalty capped at 15% on page 2.
    document = extractor.extract(_read("tenant_a/tire_supply_agreement_frontera.pdf"))

    assert "1.5%" in document.pages[1].text
    assert "15%" in document.pages[1].text


def test_corrupted_pdf_raises_corrupt(extractor):
    with pytest.raises(CorruptPDFError):
        extractor.extract(_read("tenant_a/corrupted_contract.pdf"))


def test_random_bytes_raise_corrupt(extractor):
    with pytest.raises(CorruptPDFError):
        extractor.extract(b"not a pdf at all")


def test_scanned_pdf_without_text_raises_empty(extractor):
    with pytest.raises(EmptyPDFError):
        extractor.extract(_read("tenant_b/scanned_lease_addendum_no_text.pdf"))
