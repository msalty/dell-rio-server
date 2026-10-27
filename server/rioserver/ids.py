"""Receiver-visible IDs ("fids") and device text encoding.

The Rio protocol identifies everything by a 32-bit number, written in hex in
URLs. 0x100 is reserved for "all playlists" and real IDs start at 0x110.
The low nibble is always zero, as with the original server.

    track     0x100 + track_id * 0x20
    playlist  0x110 + playlist_id * 0x20
    album     0x40000000 + album_id * 0x10   (album exposed as a playlist)
"""

from __future__ import annotations

import unicodedata

ALL_PLAYLISTS = 0x100
ALBUM_BASE = 0x40000000


def track_fid(track_id: int) -> int:
    return 0x100 + track_id * 0x20


def playlist_fid(playlist_id: int) -> int:
    return 0x110 + playlist_id * 0x20


def album_fid(album_id: int) -> int:
    return ALBUM_BASE + album_id * 0x10


def decode_fid(fid: int) -> tuple[str, int] | None:
    if fid == ALL_PLAYLISTS:
        return ("all", 0)
    if fid & ALBUM_BASE:
        if fid & 0xF:
            return None
        return ("album", (fid - ALBUM_BASE) // 0x10)
    if fid < 0x110:
        return None
    if (fid - 0x100) % 0x20 == 0:
        return ("track", (fid - 0x100) // 0x20)
    if (fid - 0x110) % 0x20 == 0:
        return ("playlist", (fid - 0x110) // 0x20)
    return None


def parse_hex(text: str) -> int | None:
    try:
        return int(text.strip().strip("/"), 16)
    except (ValueError, AttributeError):
        return None


_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "…": "...",
    "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", "•": "*", "Ł": "L", "ł": "l",
    "Œ": "OE", "œ": "oe",
}


def to_device_text(s: str, keep_layout: bool = False) -> str:
    """Reduce text to Latin-1, which is what the receiver's fonts cover.

    Tabs and line breaks become spaces unless keep_layout is set (used for
    whole protocol responses, whose fields are already cleaned).
    """
    out = []
    for ch in s or "":
        if ch in "\t\r\n":
            out.append(ch if keep_layout else " ")
            continue
        if ord(ch) < 256:
            out.append(ch if ord(ch) >= 32 else " ")
            continue
        rep = _REPLACEMENTS.get(ch)
        if rep is None:
            decomposed = unicodedata.normalize("NFKD", ch)
            rep = "".join(c for c in decomposed if ord(c) < 256 and not unicodedata.combining(c))
        out.append(rep or "?")
    return "".join(out)


def device_bytes(s: str, keep_layout: bool = False) -> bytes:
    return to_device_text(s, keep_layout).encode("latin-1", "replace")
