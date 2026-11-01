"""Estimate how much of the receiver's CPU playback needs.

qemu runs the firmware far faster than a 74 MHz ARM720T, so it can't reveal
CPU starvation. This harness builds bench/bench.c (the player's decode path
plus a Now Playing render), runs it under the Unicorn ARM emulator and counts
executed instructions per second of audio.

    python3 tools/cpubench.py            # needs ffmpeg and `pip install unicorn`
    python3 tools/cpubench.py 320        # at another bitrate (kbps)
    MAD=stock python3 tools/cpubench.py  # with libmad as released

It also checks the decoder's output against ffmpeg's floating-point decode
of the same file and reports the signal-to-noise ratio.

The ARM720T in the receiver runs at 74 MHz; with cache misses and multi-cycle
loads, stores and multiplies it manages very roughly 40 million instructions
per second, so playback should stay well under that.
"""

from __future__ import annotations

import math
import struct
import subprocess
from array import array
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
SECONDS = 3.0
BUDGET_MIPS = 40.0


def make_mp3(kbps: int) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "bench.mp3"
        # Pink noise plus tones: dense spectral content, close to worst case.
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"anoisesrc=color=pink:duration={SECONDS}:amplitude=0.3",
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}",
             "-filter_complex", "amix=inputs=2", "-ac", "2", "-ar", "44100",
             "-codec:a", "libmp3lame", "-b:a", f"{kbps}k", "-write_xing", "0", "-id3v2_version", "0",
             str(out)],
            check=True,
        )
        return out.read_bytes()


def build(data: bytes) -> Path:
    src = BUILD / "bench_mp3.c"
    BUILD.mkdir(exist_ok=True)
    body = ",".join(str(b) for b in data)
    src.write_text(
        f"const unsigned char bench_mp3[] = {{{body}}};\n"
        f"const unsigned int bench_mp3_len = {len(data)};\n"
    )
    subprocess.run(["make", "-s", "-C", str(ROOT), "build/bench"], check=True)
    return BUILD / "bench"


def symbols(elf: Path) -> dict[str, int]:
    out = subprocess.run(["arm-none-eabi-nm", str(elf)], capture_output=True, text=True, check=True).stdout
    syms = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    return syms


def run(elf: Path, mode: int) -> tuple[int, int, array]:
    from unicorn import UC_ARCH_ARM, UC_HOOK_BLOCK, UC_HOOK_INTR, UC_MODE_ARM, Uc
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_SP

    data = elf.read_bytes()
    (e_entry, e_phoff) = struct.unpack_from("<II", data, 24)
    (e_phentsize, e_phnum) = struct.unpack_from("<HH", data, 42)
    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    for i in range(e_phnum):
        p_type, off, vaddr, _, filesz, memsz = struct.unpack_from("<IIIIII", data, e_phoff + i * e_phentsize)
        if p_type != 1:
            continue
        base = vaddr & ~0xFFF
        size = ((vaddr + memsz + 0xFFF) & ~0xFFF) - base
        uc.mem_map(base, size)
        uc.mem_write(vaddr, data[off:off + filesz])
    stack_top = 0x7F000000
    uc.mem_map(stack_top - 0x100000, 0x100000)
    # argc = 0, argv = NULL, envp = NULL
    uc.mem_write(stack_top - 16, b"\0" * 16)
    uc.reg_write(UC_ARM_REG_SP, stack_top - 16)
    syms = symbols(elf)
    uc.mem_write(syms["bench_mode"], struct.pack("<i", mode))

    count = [0]

    def on_block(uc, address, size, _):
        count[0] += size // 4

    def on_intr(uc, intno, _):
        uc.emu_stop()  # the only syscall is exit()

    uc.hook_add(UC_HOOK_BLOCK, on_block)
    uc.hook_add(UC_HOOK_INTR, on_intr)
    uc.emu_start(e_entry, 0)
    frames = struct.unpack("<I", uc.mem_read(syms["bench_frames"], 4))[0]
    pcm = array("h")
    if mode == 1:
        kept = min(frames, 140)  # PCM_KEEP_FRAMES in bench.c
        pcm.frombytes(bytes(uc.mem_read(syms["bench_pcm"], kept * 1152 * 4)))
    return count[0], frames, pcm


def reference_pcm(mp3: bytes) -> array:
    """ffmpeg's floating-point decode: interleaved stereo, full scale = 1.0."""
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "mp3", "-i", "pipe:", "-f", "f32le",
                          "-ac", "2", "-ar", "44100", "pipe:"], input=mp3, capture_output=True, check=True).stdout
    ref = array("f")
    ref.frombytes(out)
    return ref


def snr_db(ours: array, ref: array) -> float:
    """Signal-to-noise ratio of our 16-bit output against the reference, once
    the two are lined up (decoders may differ in how much delay they add)."""
    ours_l = [ours[i] / 32768 for i in range(0, len(ours), 2)]
    ref_l = [ref[i] for i in range(0, len(ref), 2)]
    mid, win = len(ours_l) // 2, 2000
    best = max(range(-1500, 1501), key=lambda lag: sum(
        ours_l[mid + i] * ref_l[mid + i + lag] for i in range(0, win, 4)
        if 0 <= mid + i + lag < len(ref_l)))
    sig = err = 0.0
    for i in range(1500, len(ours) - 3000):
        j = i + 2 * best
        if 0 <= j < len(ref):
            r = ref[j]
            sig += r * r
            err += (r - ours[i] / 32768) ** 2
    return 10 * math.log10(sig / err) if err else float("inf")


def main() -> None:
    kbps = int(sys.argv[1]) if len(sys.argv) > 1 else 128
    print(f"{kbps} kbps MP3")
    mp3 = make_mp3(kbps)
    elf = build(mp3)
    audio_seconds = None
    results = {}
    for mode, label in ((0, "decode"), (2, "one Now Playing render"), (3, "one title scroll step")):
        instr, frames, _ = run(elf, mode)
        results[mode] = (instr, frames)
        if mode == 0:
            audio_seconds = frames * 1152 / 44100
        if mode >= 2:
            print(f"{label:34s} {instr / 1e6:8.3f} M instructions "
                  f"({instr * 8 / 1e6:.2f} M/s at 8 a second)")
        else:
            mips = instr / audio_seconds / 1e6
            print(f"{label:34s} {mips:8.1f} M instructions per second of audio "
                  f"({mips / BUDGET_MIPS * 100:.0f}% of ~{BUDGET_MIPS:.0f} MIPS)")
    if results[0][1] == 0:
        sys.exit("bench decoded no frames")
    _, _, pcm = run(elf, 1)
    print(f"{'accuracy against ffmpeg':34s} {snr_db(pcm, reference_pcm(mp3)):8.1f} dB signal-to-noise")


if __name__ == "__main__":
    main()
