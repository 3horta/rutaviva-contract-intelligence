"""Split an extracted document into chunks for search (provisional strategy).

Rules:
- A chunk never crosses a page boundary, so its page number is exact.
- Consecutive blocks of a page are packed together up to `max_chars`.
- A block longer than `max_chars` is split at sentence boundaries (and, for a sentence
  that is still too long, at word boundaries).
- The last `overlap_chars` of a chunk are repeated at the start of the next chunk of the
  same page, so a sentence cut between two chunks is still found whole in one of them.
- `bbox` is the union of the boxes of the blocks the chunk uses (None if none has one).
- `embed_text` adds a short context prefix (title, page, nearest heading) to `content`.

This is a pure function: no I/O, no Docling, easy to test and to tune later.
"""

import re
from dataclasses import dataclass

from app.ingestion.types import BBox, ChunkDraft, ExtractedDocument, PageText

# Block kinds that name a section. The extractor maps Docling's labels to these.
HEADING_KINDS = frozenset({"heading", "title"})

# End of a sentence: ., ! or ? followed by whitespace.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# Separators used when joining pieces into a chunk.
_NEW_BLOCK = "\n"  # between two different blocks
_SAME_BLOCK = " "  # between two pieces of the same split block


@dataclass(frozen=True)
class _Piece:
    """A block, or part of a block, ready to be packed into a chunk."""

    text: str
    bbox: BBox | None
    # Nearest heading at or before this piece (it may be the piece itself).
    heading: str | None
    # How this piece is joined to the previous one in a chunk.
    joiner: str
    # True for the text repeated from the previous chunk.
    is_overlap: bool = False


def chunk_document(
    doc: ExtractedDocument, *, title: str, max_chars: int, overlap_chars: int
) -> list[ChunkDraft]:
    """Chunks of the whole document, with `chunk_index` consecutive across pages."""
    if max_chars <= 0 or overlap_chars < 0:
        raise ValueError("max_chars must be positive and overlap_chars not negative")
    # The overlap must leave room for new text in every chunk.
    if overlap_chars * 2 >= max_chars:
        raise ValueError("overlap_chars must be less than half of max_chars")

    drafts: list[ChunkDraft] = []
    # Sections often continue on the next page, so the heading carries over.
    heading: str | None = None
    for page in doc.pages:
        pieces, heading = _page_pieces(page, heading, max_chars, overlap_chars)
        for chunk_pieces in _pack(pieces, max_chars, overlap_chars):
            drafts.append(_draft(chunk_pieces, page, len(drafts), title))
    return drafts


def _page_pieces(
    page: PageText, heading: str | None, max_chars: int, overlap_chars: int
) -> tuple[list[_Piece], str | None]:
    """The page's blocks as pieces no longer than `max_chars`, in reading order.
    Also returns the last heading seen, for the next page."""
    pieces: list[_Piece] = []
    for block in page.blocks:
        text = " ".join(block.text.split())  # collapse whitespace and newlines
        if not text:
            continue
        if block.kind in HEADING_KINDS:
            heading = text
        if len(text) <= max_chars:
            pieces.append(_Piece(text, block.bbox, heading, _NEW_BLOCK))
            continue
        # Oversized block: split it so that each part still fits next to an overlap.
        limit = max_chars - overlap_chars - len(_NEW_BLOCK)
        for i, part in enumerate(_split_text(text, limit)):
            joiner = _NEW_BLOCK if i == 0 else _SAME_BLOCK
            pieces.append(_Piece(part, block.bbox, heading, joiner))
    return pieces, heading


def _split_text(text: str, limit: int) -> list[str]:
    """Split `text` into parts of at most `limit` characters, at sentence boundaries
    when possible, then at word boundaries, and as a last resort inside a word."""
    sentences = _SENTENCE_END.split(text)
    units: list[str] = []
    for sentence in sentences:
        if len(sentence) <= limit:
            units.append(sentence)
        else:
            units.extend(_split_words(sentence, limit))
    return _greedy_join(units, limit)


def _split_words(sentence: str, limit: int) -> list[str]:
    words: list[str] = []
    for word in sentence.split():
        # A "word" longer than the limit (e.g. a long URL) is cut by characters.
        words.extend(word[i : i + limit] for i in range(0, len(word), limit))
    return _greedy_join(words, limit)


def _greedy_join(units: list[str], limit: int) -> list[str]:
    """Join consecutive units with a space while the result fits in `limit`."""
    parts: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}" if current else unit
        if len(candidate) <= limit:
            current = candidate
        else:
            parts.append(current)
            current = unit
    if current:
        parts.append(current)
    return parts


def _pack(pieces: list[_Piece], max_chars: int, overlap_chars: int) -> list[list[_Piece]]:
    """Group the pieces of one page into chunks of at most `max_chars` characters."""
    chunks: list[list[_Piece]] = []
    current: list[_Piece] = []
    for piece in pieces:
        if current and _length([*current, piece]) > max_chars:
            chunks.append(current)
            overlap = _overlap(current, overlap_chars)
            # The overlap is only carried if it fits with the next piece; a piece close
            # to `max_chars` starts a chunk on its own.
            if overlap is not None and _length([overlap, piece]) <= max_chars:
                current = [overlap]
            else:
                current = []
        current.append(piece)
    if current:
        chunks.append(current)
    return chunks


def _length(pieces: list[_Piece]) -> int:
    return len(_join(pieces))


def _join(pieces: list[_Piece]) -> str:
    text = ""
    for i, piece in enumerate(pieces):
        text += piece.text if i == 0 else piece.joiner + piece.text
    return text


def _overlap(pieces: list[_Piece], overlap_chars: int) -> _Piece | None:
    """The last `overlap_chars` of a chunk, starting at a word boundary, with the box of
    the pieces that text comes from."""
    if overlap_chars == 0:
        return None
    text = _join(pieces)
    tail = text[-overlap_chars:]
    if len(text) > overlap_chars and not text[-overlap_chars - 1].isspace():
        # The cut fell inside a word: start at the next word instead.
        first_space = tail.find(" ")
        tail = tail[first_space + 1 :] if first_space != -1 else tail
    tail = tail.strip()
    if not tail:
        return None

    # Walk back from the end until the pieces cover the tail, joining their boxes.
    bbox: BBox | None = None
    covered = 0
    for piece in reversed(pieces):
        bbox = _union(bbox, piece.bbox)
        covered += len(piece.text) + len(piece.joiner)
        if covered >= len(tail):
            break
    return _Piece(tail, bbox, pieces[-1].heading, _NEW_BLOCK, is_overlap=True)


def _union(a: BBox | None, b: BBox | None) -> BBox | None:
    if a is None:
        return b
    if b is None:
        return a
    return a.union(b)


def _draft(pieces: list[_Piece], page: PageText, chunk_index: int, title: str) -> ChunkDraft:
    content = _join(pieces)
    bbox: BBox | None = None
    for piece in pieces:
        bbox = _union(bbox, piece.bbox)
    # The heading of the first new piece: the overlap belongs to the previous chunk.
    first_new = next(piece for piece in pieces if not piece.is_overlap)
    return ChunkDraft(
        page=page.number,
        chunk_index=chunk_index,
        content=content,
        embed_text=_context_prefix(title, page.number, first_new.heading) + content,
        bbox=bbox,
        page_width=page.width,
        page_height=page.height,
    )


def _context_prefix(title: str, page: int, heading: str | None) -> str:
    lines = [f"Document: {title}", f"Page: {page}"]
    if heading:
        lines.append(f"Section: {heading}")
    return "\n".join(lines) + "\n\n"
