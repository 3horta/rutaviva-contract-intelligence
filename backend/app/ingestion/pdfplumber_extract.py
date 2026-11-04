"""PDF text extraction with pdfplumber: the default extractor.

Why this and not Docling by default: Docling runs a layout model, which on a CPU took
about 70 s per page here (minutes per contract). pdfplumber reads the PDF's text layer
directly: seconds per contract, no models, runs on every platform. What we need from
extraction is the text of each page and where it is (to cite and to highlight), and
pdfplumber gives both. Docling stays available (`TEXT_EXTRACTOR=docling`) when its
layout understanding (reading order of complex layouts, real table structure) is worth
the time.

How lines become blocks (simple, explainable rules):
- Lines come with coordinates in PDF points, origin top-left (our convention).
- Running headers and footers are dropped: a line in the top/bottom margin whose text
  (digits ignored) repeats in the margin of another page, or that is just a page number
  ("Page 2 of 3", "- 4 -"). Margin lines that do not repeat are kept: a real clause can
  be printed near the bottom of a page.
- A new block starts at a vertical gap larger than ~0.8 of a line, at a numbered clause
  ("2.3 Late delivery…") and at a heading. A heading is a short line that is all caps or
  starts with SECTION/ARTICLE/EXHIBIT/SCHEDULE/ANNEX; chunking uses it as the section name.
"""

import re
from io import BytesIO
from typing import Any

from app.core.errors import CorruptPDFError, EmptyPDFError
from app.ingestion.types import BBox, ExtractedDocument, PageText, TextBlock

# Running headers/footers live in the outer ~7% of the page height.
MARGIN_FRACTION = 0.07
# A gap above this fraction of the line height starts a new paragraph.
PARAGRAPH_GAP = 0.8
MAX_HEADING_CHARS = 90

_CLAUSE = re.compile(r"^\d+(\.\d+)*\.?\s+\S")
_PAGE_NUMBER = re.compile(r"^[-\s]*(page\s*)?\d+(\s*(of|/)\s*\d+)?[-\s]*$", re.IGNORECASE)
_HEADING_WORD = re.compile(r"^(SECTION|ARTICLE|EXHIBIT|SCHEDULE|ANNEX|APPENDIX)\b", re.IGNORECASE)


class PdfPlumberExtractor:
    """`TextExtractor` that reads the PDF text layer with pdfplumber."""

    def extract(self, pdf: bytes) -> ExtractedDocument:
        import pdfplumber  # local import: keeps module import cheap

        try:
            with pdfplumber.open(BytesIO(pdf)) as document:
                raw = [_raw_page(page) for page in document.pages]
        except Exception as exc:
            # Only the exception type: its message could quote the file.
            raise CorruptPDFError(f"the PDF could not be parsed ({type(exc).__name__})") from exc

        running = _running_lines(raw)
        pages = [
            PageText(
                number=number,
                width=width,
                height=height,
                blocks=_blocks([line for line in lines if not _is_running(line, running)]),
            )
            for number, (width, height, lines) in enumerate(raw, 1)
        ]

        extracted = ExtractedDocument(pages=pages)
        if extracted.page_count == 0:
            raise CorruptPDFError("the PDF has no pages")
        if not extracted.has_text:
            raise EmptyPDFError("no extractable text (scanned PDF? OCR is not supported)")
        return extracted


def _raw_page(page: Any) -> tuple[float, float, list[dict[str, Any]]]:
    """Page size and its text lines, each tagged with whether it sits in a margin."""
    width, height = float(page.width), float(page.height)
    margin = height * MARGIN_FRACTION
    lines = []
    for line in page.extract_text_lines():
        if not line["text"].strip():
            continue
        line["in_margin"] = line["top"] < margin or line["bottom"] > height - margin
        lines.append(line)
    return width, height, lines


def _margin_key(text: str) -> str:
    """Margin text with digits masked: "Page 2 of 3" and "Page 3 of 3" are one line."""
    return re.sub(r"\d+", "#", " ".join(text.split()).casefold())


def _running_lines(raw: list[tuple[float, float, list[dict[str, Any]]]]) -> set[str]:
    """Margin texts that appear on at least two pages: running headers and footers."""
    pages_with: dict[str, int] = {}
    for _width, _height, lines in raw:
        for key in {_margin_key(line["text"]) for line in lines if line["in_margin"]}:
            pages_with[key] = pages_with.get(key, 0) + 1
    return {key for key, count in pages_with.items() if count >= 2}


def _is_running(line: dict[str, Any], running: set[str]) -> bool:
    if not line["in_margin"]:
        return False
    text = line["text"].strip()
    return _margin_key(text) in running or _PAGE_NUMBER.match(text) is not None


def is_heading(text: str) -> bool:
    if len(text) > MAX_HEADING_CHARS or text.endswith((".", ";", ",")):
        return False
    letters = [c for c in text if c.isalpha()]
    all_caps = bool(letters) and all(c.isupper() for c in letters)
    return all_caps or bool(_HEADING_WORD.match(text))


def _blocks(lines: list[dict[str, Any]]) -> list[TextBlock]:
    """Group consecutive lines into paragraph and heading blocks."""
    blocks: list[TextBlock] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if current:
            text = " ".join(line["text"].strip() for line in current)
            box = BBox(
                min(line["x0"] for line in current),
                min(line["top"] for line in current),
                max(line["x1"] for line in current),
                max(line["bottom"] for line in current),
            )
            kind = "heading" if len(current) == 1 and is_heading(text) else "paragraph"
            blocks.append(TextBlock(text=text, bbox=box, kind=kind))
            current.clear()

    for line in lines:
        text = line["text"].strip()
        if current:
            previous = current[-1]
            line_height = max(previous["bottom"] - previous["top"], 1.0)
            gap = line["top"] - previous["bottom"]
            starts_new = (
                gap > PARAGRAPH_GAP * line_height
                or _CLAUSE.match(text) is not None
                or is_heading(text)
                or is_heading(previous["text"].strip())
            )
            if starts_new:
                flush()
        current.append(line)
    flush()
    return blocks
