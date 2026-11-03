"""Data exchanged between extraction, chunking and the ingestion task.

Coordinates are PDF points with the origin at the **top-left** of the page (the PDF.js
viewer draws with the same convention). Extractors that work bottom-left must convert.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class BBox:
    x0: float
    top: float
    x1: float
    bottom: float

    def union(self, other: "BBox") -> "BBox":
        return BBox(
            min(self.x0, other.x0),
            min(self.top, other.top),
            max(self.x1, other.x1),
            max(self.bottom, other.bottom),
        )

    def as_list(self) -> list[float]:
        return [self.x0, self.top, self.x1, self.bottom]


@dataclass(frozen=True)
class TextBlock:
    """One layout element (paragraph, heading, list item, table cell…) of a page."""

    text: str
    bbox: BBox | None
    kind: str = "paragraph"


@dataclass(frozen=True)
class PageText:
    number: int  # 1-based
    width: float
    height: float
    blocks: list[TextBlock] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.text.strip())


@dataclass(frozen=True)
class ExtractedDocument:
    pages: list[PageText]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_text(self) -> bool:
        return any(page.text.strip() for page in self.pages)


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk before it gets an embedding and is written to the database.

    `content` is what is stored, searched with full text and shown in citations.
    `embed_text` is what is embedded: `content` plus a short context prefix (contract
    title, section) that helps the vector without polluting the citation.
    A chunk never crosses a page boundary, so `page` is exact.
    """

    page: int
    chunk_index: int
    content: str
    embed_text: str
    bbox: BBox | None
    page_width: float
    page_height: float


class TextExtractor(Protocol):
    def extract(self, pdf: bytes) -> ExtractedDocument:
        """Raise `CorruptPDFError` if the file cannot be parsed and `EmptyPDFError` if
        it has no extractable text (scans: OCR is out of scope)."""
        ...
