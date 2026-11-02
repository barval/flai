"""Text extraction for the legacy binary Word format (``.doc``, Word 97-2003).

The format is an OLE2 compound document. The text lives in the ``WordDocument``
stream next to the FIB, and the stream is not necessarily contiguous: a piece
table in the table stream (``0Table`` or ``1Table``) maps ranges of character
positions to byte ranges of the file, each piece stored either as UTF-16LE or as
CP1252. Word uses the piece table for every document it saves; the contiguous
layout survives only in files written by very old versions, so both paths are
implemented here and the piece table wins when it is valid.

Only text is extracted. Formatting, table geometry and drawings are not
reconstructed — indexing and the prompt need the words, not the layout.

The one thing this module refuses is an encrypted document: its text is not
readable without the password, and a silently empty document would index as
"no content" rather than say why.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import olefile

# Word 97 files start with this identifier; anything else is not a .doc.
WORD_IDENTIFIER = 0xA5EC
# FibBase.flags: bit 8 marks an encrypted document, bit 9 selects the table stream.
FLAG_ENCRYPTED = 0x0100
FLAG_WHICH_TABLE = 0x0200
FLAG_COMPLEX = 0x0004
# Character counts and the piece-table location inside FibRgLw97 / FibRgFcLcb97.
OFFSET_CCP_TEXT = 0x004C
OFFSET_FC_CLX = 0x01A2
OFFSET_LCB_CLX = 0x01A6
FIB_MIN_SIZE = 0x01AA

# Word writes these control characters into the text stream.
FIELD_BEGIN = "\x13"
FIELD_SEPARATOR = "\x14"
FIELD_END = "\x15"


class DocExtractError(RuntimeError):
    """A .doc that cannot be read: encrypted, truncated or not a Word file."""


@dataclass(frozen=True)
class _Piece:
    """One run of characters stored at a byte offset in the WordDocument stream."""

    cp_start: int
    cp_end: int
    offset: int
    compressed: bool

    @property
    def length(self) -> int:
        return self.cp_end - self.cp_start


def extract_doc_text(data: bytes) -> str:
    """Return the text of a Word 97-2003 document.

    Raises:
        DocExtractError: the file is not a readable Word document, or is
            encrypted.
    """
    if not data:
        raise DocExtractError("empty document")
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists("WordDocument"):
                raise DocExtractError("not a Word document: no WordDocument stream")
            word = ole.openstream("WordDocument").read()
            table_name = _table_stream_name(word)
            table = ole.openstream(table_name).read() if ole.exists(table_name) else b""
    except DocExtractError:
        raise
    except Exception as e:  # olefile raises its own errors for a broken container
        raise DocExtractError(f"unreadable document container: {e}") from e

    if len(word) < FIB_MIN_SIZE or _u16(word, 0) != WORD_IDENTIFIER:
        raise DocExtractError("not a Word document: unexpected file identifier")

    flags = _u16(word, 0x0A)
    if flags & FLAG_ENCRYPTED:
        raise DocExtractError("encrypted document")

    pieces = _read_pieces(word, table)
    text = "".join(_piece_text(word, piece) for piece in pieces) if pieces else _contiguous_text(word, flags)
    return _clean(text)


def is_word_document(data: bytes) -> bool:
    """True when the data is an OLE2 container holding a Word document.

    libmagic is handed the first two kilobytes and answers with the generic
    ``application/x-ole-storage`` for most Word files — the same answer a legacy
    ``.xls`` produces — so the container itself has to decide.
    """
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists("WordDocument"):
                return False
            head = ole.openstream("WordDocument").read(2)
    except Exception:
        return False
    return len(head) == 2 and _u16(head, 0) == WORD_IDENTIFIER


def is_encrypted_doc(data: bytes) -> bool:
    """True for a password-protected document, whose text cannot be read.

    Answered at upload time so the user is told why, instead of the file being
    stored and failing to index a second later.
    """
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists("WordDocument"):
                return False
            head = ole.openstream("WordDocument").read(0x0C)
    except Exception:
        return False
    return len(head) >= 0x0C and bool(_u16(head, 0x0A) & FLAG_ENCRYPTED)


def _u16(buf: bytes, offset: int) -> int:
    return int.from_bytes(buf[offset : offset + 2], "little")


def _u32(buf: bytes, offset: int) -> int:
    return int.from_bytes(buf[offset : offset + 4], "little")


def _table_stream_name(word: bytes) -> str:
    return "1Table" if _u16(word, 0x0A) & FLAG_WHICH_TABLE else "0Table"


def _read_pieces(word: bytes, table: bytes) -> list[_Piece]:
    """Parse the piece table, or return nothing when the file has none."""
    if not table:
        return []
    fc_clx = _u32(word, OFFSET_FC_CLX)
    lcb_clx = _u32(word, OFFSET_LCB_CLX)
    if not lcb_clx or fc_clx + lcb_clx > len(table):
        return []
    clx = table[fc_clx : fc_clx + lcb_clx]

    # The CLX is a sequence of Prc records (0x01) followed by one Pcdt (0x02).
    position = 0
    while position < len(clx):
        kind = clx[position]
        if kind == 0x01:
            size = _u16(clx, position + 1)
            position += 3 + size
            continue
        if kind != 0x02:
            return []
        length = _u32(clx, position + 1)
        plc = clx[position + 5 : position + 5 + length]
        return _parse_plc(plc)
    return []


def _parse_plc(plc: bytes) -> list[_Piece]:
    """PlcPcd = (n+1) character positions followed by n 8-byte piece descriptors."""
    if len(plc) < 16 or (len(plc) - 4) % 12:
        return []
    count = (len(plc) - 4) // 12
    positions = [_u32(plc, index * 4) for index in range(count + 1)]
    descriptors = 4 * (count + 1)
    pieces: list[_Piece] = []
    for index in range(count):
        fc_raw = _u32(plc, descriptors + index * 8 + 2)
        compressed = bool(fc_raw & 0x40000000)
        offset = (fc_raw & 0x3FFFFFFF) // 2 if compressed else fc_raw
        pieces.append(
            _Piece(
                cp_start=positions[index],
                cp_end=positions[index + 1],
                offset=offset,
                compressed=compressed,
            )
        )
    return pieces


def _piece_text(word: bytes, piece: _Piece) -> str:
    length = piece.length
    if length <= 0:
        return ""
    if piece.compressed:
        chunk = word[piece.offset : piece.offset + length]
        return chunk.decode("cp1252", errors="replace")
    chunk = word[piece.offset : piece.offset + length * 2]
    return chunk.decode("utf-16-le", errors="replace")


def _contiguous_text(word: bytes, flags: int) -> str:
    """Fallback for a document without a usable piece table.

    Older writers store the text as one UTF-16 run right after the FIB, and the
    character count is the one the FIB reports.
    """
    ccp_text = _u32(word, OFFSET_CCP_TEXT)
    if ccp_text:
        start = FIB_MIN_SIZE
        chunk = word[start : start + ccp_text * 2]
        if len(chunk) >= ccp_text * 2:
            return chunk.decode("utf-16-le", errors="replace")
    if flags & FLAG_COMPLEX:
        return ""
    return word[FIB_MIN_SIZE:].decode("utf-16-le", errors="replace")


def _clean(text: str) -> str:
    """Turn Word's control characters into the plain text a reader sees.

    Field codes need care: ``\\x13`` opens an instruction, ``\\x14`` separates it
    from the displayed result and ``\\x15`` closes the field. The instruction is
    dropped and the result kept, so a page number or a cross-reference stays as
    its visible text instead of appearing as code.
    """
    out: list[str] = []
    depth = 0
    in_instruction = False
    for char in text:
        if char == FIELD_BEGIN:
            depth += 1
            in_instruction = True
            continue
        if char == FIELD_SEPARATOR:
            in_instruction = False
            continue
        if char == FIELD_END:
            depth = max(0, depth - 1)
            in_instruction = False
            continue
        if in_instruction:
            continue
        if char in "\r\x0b\x0c":
            out.append("\n")
        elif char == "\x07":
            out.append("\t")
        elif char in "\x01\x02\x05\x08":
            continue
        elif char == "\x1e":
            out.append("-")
        elif char == "\x1f":
            continue
        else:
            out.append(char)
    text = "".join(out)
    # A tab at the end of a table row carries no meaning once the grid is gone.
    text = "\n".join(line.rstrip("\t") for line in text.split("\n"))
    text = text.replace("\u0000", "")
    return text.strip()
