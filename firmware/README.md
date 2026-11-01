# RioServer NG receiver firmware

A new player for the Rio Receiver / Dell Digital Audio Receiver with
iPod-style menus. `make` produces `build/receiver-ng.arf`, which the server
hands to receivers over NFS.

## Why it has no C library

The receiver runs a Linux 2.2 ARM kernel (ARM720T, ARMv4T) that only
understands the old ARM system-call ABI ("OABI"). Today's ARM Linux
toolchains only target the newer EABI, and an old cross-toolchain is hard to
come by. So this firmware is built with a bare-metal compiler
(`arm-none-eabi-gcc -mcpu=arm7tdmi`) and carries its own small runtime:

- `src/sys.c` contains OABI system calls (`swi 0x900000+n`), string and
  memory functions, `printf`-style formatting and a first-fit allocator.
- Only 32-bit fields are passed to the kernel, so the EABI and OABI
  structure layouts never differ.
- `tools/elf_oabi.py` clears the ELF header's EABI flags, and the binary is
  linked at `0x02000000` like RioPlay's binaries for the same kernel.

The result is one static ~125 KB executable that runs as `/sbin/init`.

## Layout

| File | |
|---|---|
| `src/main.c` | event loop: remote, display, server polling, playback |
| `src/ui.c` | menus served by the server, Now Playing, Settings |
| `src/player.c` | queue, HTTP streaming, libmad decoding, resampling to 44.1 kHz |
| `rockbox/` | the parts of Rockbox's build that its libmad expects (see below) |
| `src/net.c` | sockets (`socketcall`), SSDP discovery, HTTP client |
| `src/hw.c` | `/dev/display`, `/dev/ir`, `/dev/audio`, `/dev/mixer` |
| `src/gfx.c`, `src/fonts.h` | 4-level drawing and anti-aliased fonts (`tools/mkfont.py`) |
| `src/logo.h`, `art/` | boot logo and kernel splash screens (`tools/mklogo.py`) |
| `tools/kernel_splash.py` | puts the splash screens into the RioPlay kernel |
| `tools/fetch_deps.py` | downloads the MP3 decoder and RioPlay's kernel image (checksummed) |
| `tools/mkarf.py` | packs the firmware image |
| `tools/simulate.py` | runs the firmware under `qemu-arm` with simulated hardware |
| `tools/cpubench.py`, `bench/` | measures decode and UI CPU cost (see below) |

## Simulator

```sh
make init
python3 tools/simulate.py build/init 127.0.0.1:12078 --keys select,select,down,select --shot screen.png
```

The binary is identical to the one that runs on the receiver. Only the
devices are replaced, by files named in the `RIO_SIM_*` environment
variables (see `src/hw.h`).

## Boot logo

The RioPlay kernel has three tables of 128x64 1-bit splash screens built in
(Dell, Rio and empeg) and picks one by hardware ID. `tools/kernel_splash.py`
finds the tables by their layout, swaps every screen for `art/splash*.pbm`,
and recompresses the kernel with zopfli so it fits the original gzip slot.
`server/tests/test_kernel_splash.py` runs the zImage's own decompressor under
Unicorn to check that the patched kernel still unpacks correctly.

The firmware's own boot screen draws the same logo in the same place, at four
brightness levels, so the hand-over from the kernel is seamless. To change the
logo, edit `tools/mklogo.py` and run it (needs Pillow and DejaVu fonts).

## MP3 decoder

The firmware decodes with libmad 0.15.1b as maintained by Rockbox
(`lib/rbcodec/codecs/libmad` in Rockbox's repository, fetched at a pinned
commit). Rockbox adds hand-written ARM assembly for the synthesis
filterbank, the most expensive stage, written for ARM cores of the
receiver's generation. It needs about 20% fewer instructions than libmad as
released, with the same accuracy. `make MAD=stock` builds with libmad as
released instead, for comparison.

Rockbox's libmad leaves its work buffers to the caller (`src/player.c` sets
them up) and expects a few things from Rockbox's own build: `rockbox/`
supplies them.

## CPU budget

On a real receiver a 320 kbps track kept the CPU 97% busy with libmad as
released, and its audio dropped out for 20-35 ms every few seconds: the
original SonicBlue player's decoder was much faster. The firmware reports
its CPU use, and how much of it went to decoding, the display and the
network, with each status poll, and the server logs it when it runs short.

qemu runs the firmware much faster than the receiver's 74 MHz ARM720T, so
it can't reveal CPU starvation. `tools/cpubench.py` (needs ffmpeg and
`pip install unicorn`) runs the decode path from `bench/bench.c` under the
Unicorn emulator, counts instructions, and checks the decoded audio against
ffmpeg's floating-point decoder:

```
$ python3 tools/cpubench.py 320
320 kbps MP3
decode                                 14.7 M instructions per second of audio (37% of ~40 MIPS)
one Now Playing render                0.071 M instructions (0.57 M/s at 8 a second)
one title scroll step                 0.030 M instructions (0.24 M/s at 8 a second)
accuracy against ffmpeg                71.0 dB signal-to-noise
```

The ~40 MIPS budget is a rough guess and reads optimistic: the real
receiver also spends time in its kernel and drivers. Anything that runs once
per MP3 frame (about 38 times a second) has to stay cheap. An early version
shifted the whole 96 KB input buffer after every frame, which roughly
doubled the CPU load. The display matters too: Now Playing scrolls a long
title about 8 times a second, so a scroll step redraws only the title line,
and text drawing avoids per-pixel division and clipping.

## Licence

GPL v2 or later (see `COPYING`), because the firmware links libmad (with
Rockbox's changes, also GPL) and its hardware interface follows RioPlay. The fonts are under the Bitstream Vera
licence (see `LICENSE.fonts`).
