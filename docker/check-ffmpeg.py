"""Check the image's ffmpeg can do everything the equaliser asks of it.

The image builds ffmpeg with only the parts eq.command() uses, so a part left
out shows up here rather than as silence on a receiver. Run in the image:

    docker run --rm -e PYTHONPATH=/app -v "$PWD/docker:/check:ro" rioserver-ng python /check/check-ffmpeg.py
"""

import math
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import zlib
from pathlib import Path

from rioserver import eq


def png(path: Path) -> None:
    """A tiny PNG, to embed as cover art the way many libraries have it."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\0" + b"\x80\x40\x20" * 8 for _ in range(8))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def id3_size(data: bytes) -> int:
    if data[:3] != b"ID3":
        return 0
    s = data[6:10]
    return 10 + (s[0] << 21 | s[1] << 14 | s[2] << 7 | s[3])


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg is not installed")
        return 1
    tmp = Path(tempfile.mkdtemp())
    rate, seconds = 44100, 4
    with wave.open(str(tmp / "tone.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<hh", int(8000 * math.sin(i * 440 * 2 * math.pi / rate)),
                                           int(8000 * math.sin(i * 660 * 2 * math.pi / rate)))
                               for i in range(rate * seconds)))
    png(tmp / "cover.png")
    mp3 = tmp / "tone.mp3"
    subprocess.run(["lame", "--quiet", "-b", "192", "--tt", "Tone", "--ti", str(tmp / "cover.png"),
                    str(tmp / "tone.wav"), str(mp3)], check=True)
    data = mp3.read_bytes()
    t = {"duration_ms": seconds * 1000, "size": len(data), "data_offset": id3_size(data),
         "sample_rate": rate, "channels": 2}
    # Every filter filters() can use: preamp, boosted and cut bands, balance,
    # and the limiter that comes with a boost.
    s = eq.clean({"on": True, "preamp": 3, "bands": [6, -3, 0, 2, 0, 0, -4, 0, 3, 0], "bal": 0.3})
    chain = eq.filters(s, 2, rate)
    for name in ("volume", "equalizer", "pan", "alimiter"):
        assert name in chain, f"{name} missing from {chain}"
    kbps, _ = eq.plan(t, exact=True)
    failed = False
    for at in (0.0, 1.5):
        out = subprocess.run(eq.command(ffmpeg, mp3, t, s, kbps, at), capture_output=True)
        frames_ok = out.stdout[:2] in (b"\xff\xfb", b"\xff\xfa")
        expect = (seconds - at) * kbps * 125
        ok = out.returncode == 0 and frames_ok and len(out.stdout) > 0.8 * expect
        failed |= not ok
        print(f"{'ok' if ok else 'FAILED'}: from {at} s, {len(out.stdout)} bytes at {kbps} kbps"
              f" (about {int(expect)} expected){': ' + out.stderr.decode(errors='replace').strip() if not ok else ''}")
    print("filters:", chain)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
