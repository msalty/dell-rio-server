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
| `src/net.c` | sockets (`socketcall`), SSDP discovery, HTTP client |
| `src/hw.c` | `/dev/display`, `/dev/ir`, `/dev/audio`, `/dev/mixer` |
| `src/gfx.c`, `src/fonts.h` | 4-level drawing and anti-aliased fonts (`tools/mkfont.py`) |
| `tools/fetch_deps.py` | downloads libmad and RioPlay's kernel image (checksummed) |
| `tools/mkarf.py` | packs the firmware image |
| `tools/simulate.py` | runs the firmware under `qemu-arm` with simulated hardware |

## Simulator

```sh
make init
python3 tools/simulate.py build/init 127.0.0.1:12078 --keys select,select,down,select --shot screen.png
```

The binary is identical to the one that runs on the receiver. Only the
devices are replaced, by files named in the `RIO_SIM_*` environment
variables (see `src/hw.h`).

## Licence

GPL v2 or later (see `COPYING`), because the firmware links libmad and its
hardware interface follows RioPlay. The fonts are under the Bitstream Vera
licence (see `LICENSE.fonts`).
