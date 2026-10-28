"""Replace the splash screens built into the receiver's kernel.

The Linux 2.2 kernel the firmware boots (taken from RioPlay's image) draws a
logo as soon as its display driver starts: "DELL" on a Dell Digital Audio
Receiver, "Rio" on a Rio Receiver. It carries one table of three 128x64
1-bit screens per brand and picks a table from the hardware ID.

This tool decompresses the zImage, swaps every screen in both tables for the
images in art/splash<N>.pbm (see tools/mklogo.py), recompresses the kernel
and puts it back in the slot the old one occupied. The gzip stream has to fit
that slot, so it is recompressed with zopfli when available (about 4% smaller
than gzip -9, which is what the original used).

The receiver's boot loader in flash keeps its own copy of screen 0, so that
one still appears briefly at power-on; everything after the kernel download
shows the new screens.

    python3 tools/kernel_splash.py zImage art/ -o zImage.new
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

SCREEN_BYTES = 128 * 64 // 8
SCREENS = 3
ZIMAGE_MAGIC = 0x016F2818


class SplashError(Exception):
    pass


def read_pbm(path: Path) -> bytes:
    """Read a 128x64 binary PBM; returns kernel bitmap bytes (1 = lit)."""
    data = path.read_bytes()
    fields, pos = [], 0
    while len(fields) < 3:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos)
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[pos:end])
        pos = end
    pos += 1
    if fields != [b"P4", b"128", b"64"]:
        raise SplashError(f"{path}: expected a 128x64 binary PBM")
    bits = data[pos:pos + SCREEN_BYTES]
    if len(bits) != SCREEN_BYTES:
        raise SplashError(f"{path}: truncated")
    # In PBM 1 means black; on the display 1 means lit.
    return bytes(b ^ 0xFF for b in bits)


def find_tables(kernel: bytes, base: int) -> list[int]:
    """Offsets of the splash tables: three pointers to consecutive 1 KB
    screens, stored immediately after the screens themselves."""
    tables = []
    for off in range(3 * SCREEN_BYTES, len(kernel) - 12, 4):
        p0, p1, p2 = struct.unpack_from("<3I", kernel, off)
        if (p1 == p0 + SCREEN_BYTES and p2 == p1 + SCREEN_BYTES
                and p0 == base + off - 3 * SCREEN_BYTES):
            tables.append(off)
    return tables


def gzip_slot(zimage: bytes) -> tuple[int, int]:
    """(start, end) of the compressed kernel inside the zImage."""
    start = zimage.find(b"\x1f\x8b\x08")
    while start >= 0:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            d.decompress(zimage[start:])
            if d.eof:
                return start, len(zimage) - len(d.unused_data)
        except zlib.error:
            pass
        start = zimage.find(b"\x1f\x8b\x08", start + 1)
    raise SplashError("no compressed kernel found in zImage")


def compress(data: bytes) -> bytes:
    zopfli = shutil.which("zopfli")
    if zopfli:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "Image"
            src.write_bytes(data)
            return subprocess.run([zopfli, "-c", "--gzip", str(src)], check=True,
                                  stdout=subprocess.PIPE).stdout
    c = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS, 9)
    return c.compress(data) + c.flush()


def patch(zimage: bytes, screens: list[bytes]) -> bytes:
    if struct.unpack_from("<I", zimage, 0x24)[0] != ZIMAGE_MAGIC:
        raise SplashError("not an ARM zImage")
    base = struct.unpack_from("<I", zimage, 0x28)[0]  # kernel load address
    start, end = gzip_slot(zimage)
    kernel = bytearray(zlib.decompress(zimage[start:end], 16 + zlib.MAX_WBITS))
    tables = find_tables(kernel, base)
    if not tables:
        raise SplashError("splash screen tables not found in kernel")
    for table in tables:
        for i in range(SCREENS):
            off = table - (SCREENS - i) * SCREEN_BYTES
            kernel[off:off + SCREEN_BYTES] = screens[i]
    packed = compress(bytes(kernel))
    if len(packed) > end - start:
        raise SplashError(f"recompressed kernel is {len(packed)} bytes, slot holds {end - start}"
                          + ("" if shutil.which("zopfli") else " (install zopfli)"))
    out = zimage[:start] + packed + bytes(end - start - len(packed)) + zimage[end:]
    # The decompressor stops at the end of the gzip stream; check what it will see.
    if zlib.decompress(out[start:end], 16 + zlib.MAX_WBITS) != kernel or len(out) != len(zimage):
        raise SplashError("verification of the patched zImage failed")
    return out


def load_screens(art: Path) -> list[bytes]:
    return [read_pbm(art / f"splash{i}.pbm") for i in range(SCREENS)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zimage")
    ap.add_argument("art", help="directory with splash0.pbm .. splash2.pbm")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    try:
        out = patch(Path(args.zimage).read_bytes(), load_screens(Path(args.art)))
    except SplashError as exc:
        sys.exit(f"kernel_splash: {exc}")
    Path(args.output).write_bytes(out)


if __name__ == "__main__":
    main()
