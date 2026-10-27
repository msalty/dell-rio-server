"""Assemble a receiver firmware image (receiver.arf).

The image is a tar archive of the receiver's root filesystem, served to the
receiver over NFS. It contains:

  /zImage         Linux 2.2.14 kernel for the receiver (from RioPlay)
  /il-binary.o    HomePNA network driver loaded by the kernel (from RioPlay)
  /sbin/init      the RioServer NG player (statically linked, no libc)
  /dev/...        device nodes for the display, IR, audio and console
  /proc, /tmp     mount points
"""

from __future__ import annotations

import argparse
import io
import tarfile
import time

DEVICES = [
    # name, major, minor
    ("console", 5, 1),
    ("tty", 5, 0),
    ("ttyS0", 4, 64),
    ("ttyS1", 4, 65),
    ("null", 1, 3),
    ("zero", 1, 5),
    ("random", 1, 8),
    ("urandom", 1, 9),
    ("display", 244, 0),
    ("ir", 242, 0),
    ("mixer", 245, 0),
    ("dsp", 245, 3),
    ("audio", 245, 4),
]
FROM_KERNEL_IMAGE = ["zImage", "il-binary.o"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel-from", required=True, help="existing .arf to take zImage/il-binary.o from")
    ap.add_argument("--init", required=True, help="player binary to install as /sbin/init")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    now = int(time.time())
    out = io.BytesIO()
    with tarfile.open(args.kernel_from) as src, tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tf:
        def add_dir(name: str) -> None:
            ti = tarfile.TarInfo(name)
            ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, now
            tf.addfile(ti)

        def add_file(name: str, data: bytes, mode: int) -> None:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode, ti.mtime = len(data), mode, now
            tf.addfile(ti, io.BytesIO(data))

        add_dir("./")
        members = {m.name.lstrip("./"): m for m in src.getmembers()}
        for name in FROM_KERNEL_IMAGE:
            m = members.get(name)
            if m is None:
                raise SystemExit(f"{args.kernel_from} has no {name}")
            add_file("./" + name, src.extractfile(m).read(), 0o644)
        for d in ("bin", "dev", "etc", "proc", "sbin", "tmp"):
            add_dir(f"./{d}/")
        with open(args.init, "rb") as f:
            add_file("./sbin/init", f.read(), 0o755)
        add_file("./etc/fstab", b"none /proc proc defaults 0 0\n", 0o644)
        add_file("./etc/rioserver-ng", b"RioServer NG receiver firmware\n", 0o644)
        for name, major, minor in DEVICES:
            ti = tarfile.TarInfo(f"./dev/{name}")
            ti.type, ti.mode, ti.mtime = tarfile.CHRTYPE, 0o666, now
            ti.devmajor, ti.devminor = major, minor
            tf.addfile(ti)
    with open(args.output, "wb") as f:
        f.write(out.getvalue())
    print(f"wrote {args.output} ({len(out.getvalue())} bytes)")


if __name__ == "__main__":
    main()
