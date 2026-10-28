"""The firmware image's kernel shows the RioServer NG splash screens.

Skipped unless the firmware image has been built (make -C firmware). The
decompressor test also needs `pip install unicorn`.
"""

import io
import sys
import tarfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "firmware"
IMAGE = FW / "build" / "receiver-ng.arf"
sys.path.insert(0, str(FW / "tools"))

pytestmark = pytest.mark.skipif(not IMAGE.exists(), reason="needs a firmware build (make -C firmware)")

LOAD = 0xC0028000  # where the receiver's boot loader puts the zImage


def zimage() -> bytes:
    with tarfile.open(IMAGE) as tf:
        return tf.extractfile("./zImage").read()


def test_every_splash_table_has_the_new_screens():
    import kernel_splash as ks

    z = zimage()
    start, end = ks.gzip_slot(z)
    kernel = zlib.decompress(z[start:end], 31)
    tables = ks.find_tables(kernel, LOAD)
    assert len(tables) == 3  # Dell, Rio and empeg
    screens = ks.load_screens(FW / "art")
    for table in tables:
        for i, screen in enumerate(screens):
            off = table - (3 - i) * ks.SCREEN_BYTES
            assert kernel[off:off + ks.SCREEN_BYTES] == screen


def test_kernel_decompressor_accepts_the_patched_kernel():
    """Run the zImage's own 2002-era decompressor and check its output."""
    unicorn = pytest.importorskip("unicorn")
    from unicorn import arm_const as A

    import kernel_splash as ks

    z = zimage()
    start, end = ks.gzip_slot(z)
    expect = zlib.decompress(z[start:end], 31)
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
    mu.mem_map(0xC0000000, 32 << 20)  # DRAM
    mu.mem_write(LOAD, z)

    def io_page(uc, access, addr, size, value, data):
        uc.mem_map(addr & ~0xFFFFF, 0x100000)  # memory-mapped I/O: absorb
        return True

    entries = []

    def at_entry(uc, addr, size, data):
        entries.append(addr)
        if len(entries) == 2:  # relocated kernel starts where the zImage did
            uc.emu_stop()

    mu.hook_add(unicorn.UC_HOOK_MEM_UNMAPPED, io_page)
    mu.hook_add(unicorn.UC_HOOK_CODE, at_entry, begin=LOAD, end=LOAD)
    mu.reg_write(A.UC_ARM_REG_R0, 0)
    mu.emu_start(LOAD, 0xFFFFFFFF, count=2_000_000_000)
    assert len(entries) == 2, "decompressor never jumped to the kernel"
    assert bytes(mu.mem_read(LOAD, len(expect))) == expect


def test_pbm_round_trip(tmp_path):
    import kernel_splash as ks

    bits = bytes(range(256)) * 4
    p = tmp_path / "s.pbm"
    p.write_bytes(b"P4\n# comment\n128 64\n" + bytes(b ^ 0xFF for b in bits))
    assert ks.read_pbm(p) == bits
    with pytest.raises(ks.SplashError):
        (tmp_path / "bad.pbm").write_bytes(b"P4\n64 64\n" + bytes(512))
        ks.read_pbm(tmp_path / "bad.pbm")
