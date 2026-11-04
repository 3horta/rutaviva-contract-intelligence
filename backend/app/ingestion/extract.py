"""PDF text extraction with Docling (layout-aware: reading order, headings, tables).

Docling is imported inside the class, not at module level: it is only installed in the
worker image (Linux), and the API and the tests on other platforms must still be able
to import this module.

Choices:
- `do_ocr=False`: OCR is out of scope. A scan has no text layer, so it ends
  `empty_pdf` instead of being slowly and unreliably recognised.
- Table structure OFF: the table-structure model is the slowest stage on CPU (a 3-page
  contract took minutes). Tables are still extracted, as text in reading order, which
  keeps rates and penalties searchable and citable; only the cell grid is lost.
- Coordinates are converted to the **top-left** origin (PDF points), the convention of
  `app.ingestion.types` and of the PDF viewer.
"""

from io import BytesIO
from pathlib import Path
from typing import Any

from app.core.errors import CorruptPDFError, EmptyPDFError
from app.ingestion.types import BBox, ExtractedDocument, PageText, TextBlock

# Docling labels -> block kinds used by chunking. Anything else is a paragraph.
_KINDS = {
    "title": "title",
    "section_header": "heading",
    "list_item": "list_item",
    "table": "table",
    "caption": "caption",
    "footnote": "footnote",
    "page_header": "page_header",
    "page_footer": "page_footer",
}


class DoclingExtractor:
    """`TextExtractor` backed by Docling's PDF pipeline."""

    def __init__(self, artifacts_path: Path | None = None) -> None:
        # Models are baked into the worker image; None means Docling's default cache.
        self._artifacts_path = artifacts_path
        self._converter: Any = None

    def extract(self, pdf: bytes) -> ExtractedDocument:
        from docling.datamodel.base_models import ConversionStatus, DocumentStream

        try:
            result = self._get_converter().convert(
                DocumentStream(name="document.pdf", stream=BytesIO(pdf)),
                raises_on_error=False,
            )
        except Exception as exc:
            # Only the exception type: its message could quote the file.
            raise CorruptPDFError(f"the PDF could not be parsed ({type(exc).__name__})") from exc
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            raise CorruptPDFError(f"the PDF could not be parsed (status {result.status.value})")

        extracted = _to_extracted(result.document)
        if extracted.page_count == 0:
            raise CorruptPDFError("the PDF has no pages")
        if not extracted.has_text:
            raise EmptyPDFError("no extractable text (scanned PDF? OCR is not supported)")
        return extracted

    def _get_converter(self) -> Any:
        """Built on first use and reused: loading the layout and table models is the
        slow part, not converting a page."""
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(
                artifacts_path=self._artifacts_path,
                do_ocr=False,
                do_table_structure=False,
            )
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter


def _to_extracted(document: Any) -> ExtractedDocument:
    """DoclingDocument -> ExtractedDocument: one PageText per page, its blocks in
    reading order."""
    from docling_core.types.doc import TableItem, TextItem

    sizes = {number: page.size for number, page in document.pages.items()}
    blocks: dict[int, list[TextBlock]] = {number: [] for number in sizes}

    # `iterate_items` walks the body in reading order (pictures and groups included).
    for item, _level in document.iterate_items():
        if isinstance(item, TableItem):
            text = _table_text(item)
        elif isinstance(item, TextItem):
            text = item.text
        else:
            continue
        if not text.strip() or not item.prov:
            continue
        # An item split across pages has one provenance per page; the first one gives
        # where it starts, which is what a citation needs.
        prov = item.prov[0]
        if prov.page_no not in sizes:
            continue
        height = sizes[prov.page_no].height
        box = prov.bbox.to_top_left_origin(page_height=height)
        blocks[prov.page_no].append(
            TextBlock(
                text=text,
                bbox=BBox(x0=box.l, top=box.t, x1=box.r, bottom=box.b),
                kind=_KINDS.get(str(item.label.value), "paragraph"),
            )
        )

    return ExtractedDocument(
        pages=[
            PageText(
                number=number,
                width=sizes[number].width,
                height=sizes[number].height,
                blocks=blocks[number],
            )
            for number in sorted(sizes)
        ]
    )


def _table_text(table: Any) -> str:
    """A table as plain text: one line per row, cells separated by ' | '."""
    rows = []
    for row in table.data.grid:
        cells = [cell.text.strip() for cell in row]
        # Spanning cells repeat in every position they cover; keep each one once.
        unique = [c for i, c in enumerate(cells) if c and (i == 0 or c != cells[i - 1])]
        if unique:
            rows.append(" | ".join(unique))
    return "\n".join(rows)
