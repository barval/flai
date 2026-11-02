"""A Word 97 .doc built byte by byte, for the tests that need a real file.

No Word and no converter: the OLE2 container, the FIB, the piece table and the
text are all written here, so a test exercises the real layout without shipping a
binary fixture.
"""

import struct

SECTOR = 512
FREE = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF


def _dir_entry(name: str, obj_type: int, start: int, size: int, child: int = NOSTREAM, right: int = NOSTREAM) -> bytes:
    raw = bytearray(128)
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    raw[0 : len(encoded)] = encoded
    struct.pack_into("<H", raw, 64, len(encoded))
    raw[66] = obj_type
    raw[67] = 1  # black: a colour flag olefile ignores when reading
    struct.pack_into("<I", raw, 68, NOSTREAM)  # left sibling
    struct.pack_into("<I", raw, 72, right)
    struct.pack_into("<I", raw, 76, child)
    struct.pack_into("<I", raw, 116, start)
    struct.pack_into("<Q", raw, 120, size)
    return bytes(raw)


def build_doc(text: str, *, compressed: bool = False, encrypted: bool = False) -> bytes:
    """One Word 97 document: a FIB, a piece table and the text, in an OLE2 file."""
    if compressed:
        payload = text.encode("cp1252")
        fc = (0x400 * 2) | 0x40000000  # bit 30 marks a single-byte piece
    else:
        payload = text.encode("utf-16-le")
        fc = 0x400

    clx_offset = 0x200
    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, fc, 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc

    word = bytearray(4096)
    struct.pack_into("<H", word, 0x00, 0xA5EC)
    struct.pack_into("<H", word, 0x02, 0x00C1)
    flags = 0x0200  # 1Table
    if encrypted:
        flags |= 0x0100
    struct.pack_into("<H", word, 0x0A, flags)
    struct.pack_into("<I", word, 0x4C, len(text))  # ccpText
    struct.pack_into("<I", word, 0x1A2, clx_offset)
    struct.pack_into("<I", word, 0x1A6, len(clx))
    word[0x400 : 0x400 + len(payload)] = payload

    table = bytearray(4096)
    table[clx_offset : clx_offset + len(clx)] = clx

    # Sector 0 FAT, 1 directory, 2..9 WordDocument, 10..17 1Table.
    fat = [FREE] * 128
    fat[0] = FATSECT
    fat[1] = ENDOFCHAIN
    for index in range(2, 9):
        fat[index] = index + 1
    fat[9] = ENDOFCHAIN
    for index in range(10, 17):
        fat[index] = index + 1
    fat[17] = ENDOFCHAIN
    fat_sector = b"".join(struct.pack("<I", value) for value in fat)

    directory = b"".join(
        [
            _dir_entry("Root Entry", 5, ENDOFCHAIN, 0, child=1),
            _dir_entry("WordDocument", 2, 2, len(word), right=2),
            _dir_entry("1Table", 2, 10, len(table)),
            bytes(128),
        ]
    )

    header = bytearray(SECTOR)
    header[0:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<H", header, 0x18, 0x003E)
    struct.pack_into("<H", header, 0x1A, 0x0003)
    struct.pack_into("<H", header, 0x1C, 0xFFFE)
    struct.pack_into("<H", header, 0x1E, 9)  # 512-byte sectors
    struct.pack_into("<H", header, 0x20, 6)  # 64-byte mini sectors
    struct.pack_into("<I", header, 0x2C, 1)
    struct.pack_into("<I", header, 0x30, 1)
    struct.pack_into("<I", header, 0x38, 4096)
    struct.pack_into("<I", header, 0x3C, ENDOFCHAIN)
    struct.pack_into("<I", header, 0x40, 0)
    struct.pack_into("<I", header, 0x44, ENDOFCHAIN)
    struct.pack_into("<I", header, 0x48, 0)
    struct.pack_into("<I", header, 0x4C, 0)
    for slot in range(1, 109):
        struct.pack_into("<I", header, 0x4C + slot * 4, FREE)

    return bytes(header) + fat_sector + directory + bytes(word) + bytes(table)
