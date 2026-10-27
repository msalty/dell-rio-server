"""Generate a small tagged MP3 library for tests and demos (needs ffmpeg).

    python -m tests.make_library /tmp/music
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ALBUMS = [
    # (album artist, album, year, genre, [(track artist or None, title), ...])
    ("The Beatles", "Abbey Road", 1969, "Rock",
     [(None, "Come Together"), (None, "Something"), (None, "Maxwell's Silver Hammer")]),
    ("The Beatles", "Revolver", 1966, "Rock",
     [(None, "Taxman"), (None, "Eleanor Rigby")]),
    ("Björk", "Homogenic", 1997, "Electronic",
     [(None, "Hunter"), (None, "Jóga")]),
    ("Various Artists", "Jazz Classics", 1959, "Jazz",
     [("Miles Davis", "So What"), ("Dave Brubeck", "Take Five"), ("John Coltrane", "Naima")]),
]


def make(out: Path, seconds: float = 2.0) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    made = []
    freq = 220
    for album_artist, album, year, genre, tracks in ALBUMS:
        d = out / album_artist / album
        d.mkdir(parents=True, exist_ok=True)
        # Deliberately name files so alphabetical != track order.
        for n, (artist, title) in reversed(list(enumerate(tracks, 1))):
            f = d / f"{title}.mp3"
            freq += 55
            meta = {
                "title": title, "artist": artist or album_artist, "album": album,
                "album_artist": album_artist, "date": str(year), "genre": genre,
                "track": f"{n}/{len(tracks)}",
            }
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                   f"sine=frequency={freq}:duration={seconds}", "-ac", "2", "-ar", "44100",
                   "-codec:a", "libmp3lame", "-b:a", "128k", "-id3v2_version", "3"]
            for k, v in meta.items():
                cmd += ["-metadata", f"{k}={v}"]
            subprocess.run(cmd + [str(f)], check=True)
            made.append(f)
    _add_cover(out / "The Beatles" / "Abbey Road")
    (out / "Road Trip.m3u").write_text(
        "#EXTM3U\nVarious Artists/Jazz Classics/So What.mp3\nThe Beatles/Abbey Road/Something.mp3\n")
    return made


def _add_cover(album_dir: Path) -> None:
    """Write a folder.jpg so the album-art code paths get exercised."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    img = Image.new("RGB", (300, 300), (32, 120, 160))
    d = ImageDraw.Draw(img)
    for i in range(0, 300, 30):
        d.rectangle([i, 210, i + 18, 240], fill=(240, 240, 230))
    img.save(album_dir / "folder.jpg", quality=85)


if __name__ == "__main__":
    make(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("music"))
