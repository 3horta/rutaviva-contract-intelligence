"""Chunking is a pure function over hand-built documents: no Docling needed."""

import pytest

from app.ingestion.chunking import chunk_document
from app.ingestion.types import BBox, ExtractedDocument, PageText, TextBlock


def _page(number: int, *blocks: TextBlock) -> PageText:
    return PageText(number=number, width=612.0, height=792.0, blocks=list(blocks))


def _block(text: str, bbox: BBox | None = None, kind: str = "paragraph") -> TextBlock:
    return TextBlock(text=text, bbox=bbox, kind=kind)


def _chunk(doc: ExtractedDocument, max_chars: int = 200, overlap_chars: int = 0):
    return chunk_document(
        doc, title="Tire Supply", max_chars=max_chars, overlap_chars=overlap_chars
    )


def test_empty_document_gives_no_chunks():
    assert _chunk(ExtractedDocument(pages=[])) == []
    # Pages with only blank blocks are empty too.
    blank = ExtractedDocument(pages=[_page(1, _block("   "), _block("\n"))])
    assert _chunk(blank) == []


def test_small_blocks_are_packed_into_one_chunk():
    doc = ExtractedDocument(pages=[_page(1, _block("First clause."), _block("Second clause."))])

    [chunk] = _chunk(doc)

    assert chunk.content == "First clause.\nSecond clause."
    assert chunk.page == 1
    assert chunk.chunk_index == 0
    assert (chunk.page_width, chunk.page_height) == (612.0, 792.0)


def test_blocks_that_do_not_fit_start_a_new_chunk():
    blocks = [_block(f"Clause {i}. " + "x" * 80) for i in range(5)]
    doc = ExtractedDocument(pages=[_page(1, *blocks)])

    chunks = _chunk(doc, max_chars=200)

    assert all(len(c.content) <= 200 for c in chunks)
    # 5 blocks of ~90 chars, two per chunk.
    assert len(chunks) == 3
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_chunks_never_cross_pages_and_indexes_continue():
    doc = ExtractedDocument(
        pages=[
            _page(1, _block("Page one text.")),
            _page(2, _block("Page two text."), _block("More of page two.")),
        ]
    )

    chunks = _chunk(doc)

    assert [(c.page, c.chunk_index) for c in chunks] == [(1, 0), (2, 1)]
    assert chunks[0].content == "Page one text."
    assert chunks[1].content == "Page two text.\nMore of page two."


def test_oversized_block_is_split_at_sentence_boundaries():
    sentences = [f"Sentence number {i} says something about deliveries." for i in range(10)]
    doc = ExtractedDocument(pages=[_page(1, _block(" ".join(sentences)))])

    chunks = _chunk(doc, max_chars=200)

    assert len(chunks) > 1
    assert all(len(c.content) <= 200 for c in chunks)
    # Every chunk ends at the end of a sentence.
    assert all(c.content.endswith(".") for c in chunks)
    # No text is lost (without overlap, the parts rebuild the block).
    assert " ".join(c.content for c in chunks) == " ".join(sentences)


def test_sentence_longer_than_max_is_split_at_words():
    doc = ExtractedDocument(pages=[_page(1, _block("word " * 100))])

    chunks = _chunk(doc, max_chars=150)

    assert all(len(c.content) <= 150 for c in chunks)
    assert all(not c.content.startswith(" ") and "wor d" not in c.content for c in chunks)
    assert sum(c.content.count("word") for c in chunks) == 100


def test_overlap_repeats_the_tail_of_the_previous_chunk_on_the_same_page():
    blocks = [_block(f"Clause {i} " + "lorem ipsum " * 7) for i in range(4)]
    doc = ExtractedDocument(pages=[_page(1, *blocks)])

    chunks = _chunk(doc, max_chars=200, overlap_chars=40)

    assert len(chunks) >= 2
    assert all(len(c.content) <= 200 for c in chunks)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        overlap = current.content.split("\n")[0]
        assert 0 < len(overlap) <= 40
        assert previous.content.endswith(overlap)
        # The overlap starts at a word boundary.
        assert previous.content[-len(overlap) - 1].isspace()


def test_overlap_is_not_carried_to_the_next_page():
    doc = ExtractedDocument(
        pages=[_page(1, _block("End of page one.")), _page(2, _block("Start of page two."))]
    )

    chunks = _chunk(doc, max_chars=200, overlap_chars=50)

    assert chunks[1].content == "Start of page two."


def test_overlap_with_split_blocks_keeps_size_limit():
    text = " ".join(f"Sentence {i} about the fuel surcharge index." for i in range(30))
    doc = ExtractedDocument(pages=[_page(1, _block(text))])

    chunks = _chunk(doc, max_chars=300, overlap_chars=60)

    assert len(chunks) > 2
    assert all(len(c.content) <= 300 for c in chunks)


def test_bbox_is_the_union_of_the_blocks_used():
    doc = ExtractedDocument(
        pages=[
            _page(
                1,
                _block("Top block.", BBox(72, 100, 300, 120)),
                _block("No coordinates."),
                _block("Lower block.", BBox(60, 130, 540, 160)),
            )
        ]
    )

    [chunk] = _chunk(doc)

    assert chunk.bbox == BBox(60, 100, 540, 160)


def test_bbox_is_none_when_no_block_has_one():
    doc = ExtractedDocument(pages=[_page(1, _block("a"), _block("b"))])

    [chunk] = _chunk(doc)

    assert chunk.bbox is None


def test_embed_text_has_title_page_and_nearest_heading():
    doc = ExtractedDocument(
        pages=[
            _page(
                1,
                _block("Intro without a section."),
            ),
            _page(
                2,
                _block("4. Delivery and Penalties", kind="heading"),
                _block("Delivery within 72 business hours."),
            ),
            _page(3, _block("The heading carries over to the next page.")),
        ]
    )

    chunks = _chunk(doc)

    assert chunks[0].embed_text == "Document: Tire Supply\nPage: 1\n\nIntro without a section."
    assert chunks[1].embed_text.startswith(
        "Document: Tire Supply\nPage: 2\nSection: 4. Delivery and Penalties\n\n"
    )
    assert chunks[1].embed_text.endswith(chunks[1].content)
    assert "Section: 4. Delivery and Penalties" in chunks[2].embed_text
    # The prefix is only for the vector: the stored content has no prefix.
    assert "Document:" not in chunks[1].content


def test_whitespace_inside_blocks_is_collapsed():
    doc = ExtractedDocument(pages=[_page(1, _block("Line one\nline   two"))])

    [chunk] = _chunk(doc)

    assert chunk.content == "Line one line two"


def test_rejects_overlap_that_leaves_no_room():
    doc = ExtractedDocument(pages=[_page(1, _block("text"))])
    with pytest.raises(ValueError):
        _chunk(doc, max_chars=200, overlap_chars=100)


def test_is_deterministic():
    doc = ExtractedDocument(pages=[_page(1, *[_block("Clause text. " * 9) for _ in range(6)])])

    assert _chunk(doc, overlap_chars=30) == _chunk(doc, overlap_chars=30)
