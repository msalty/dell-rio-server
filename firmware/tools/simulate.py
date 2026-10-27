"""Run the receiver firmware under qemu-arm with simulated hardware.

The firmware binary is the exact one shipped to receivers; only its devices
are swapped for files (see src/hw.h). Useful for development and for the
end-to-end tests:

    python3 tools/simulate.py build/init 127.0.0.1:12078 --keys down,select --shot out.png

Keys: up down select back play stop next prev volup voldown random list power
      wheelup wheeldown (front-panel wheel)
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path

KEYCODES = {
    "up": 0x18C, "down": 0x186, "select": 0x182, "back": 0x188, "play": 0x185,
    "stop": 0x190, "next": 0x187, "prev": 0x183, "volup": 0x18A, "voldown": 0x181,
    "random": 0x189, "list": 0x19D, "search": 0x19F, "power": 0x184,
    "wheelup": 0x28001, "wheeldown": 0x28000, "wheelpush": 0x10010, "menu": 0x10012,
}


class Receiver:
    def __init__(self, binary: str, server: str, workdir: str | None = None, qemu: str = "qemu-arm"):
        self.dir = Path(workdir or tempfile.mkdtemp(prefix="riosim-"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.display = self.dir / "display.bin"
        self.ir = self.dir / "ir.fifo"
        self.audio = self.dir / "audio.pcm"
        self.log = self.dir / "console.log"
        if not self.ir.exists():
            os.mkfifo(self.ir)
        env = dict(os.environ, RIO_SIM_DISPLAY=str(self.display), RIO_SIM_IR=str(self.ir),
                   RIO_SIM_AUDIO=str(self.audio), RIO_SERVER=server)
        self.proc = subprocess.Popen([qemu, binary], env=env, stdout=open(self.log, "wb"),
                                     stderr=subprocess.STDOUT)
        self._irfd = None

    def press(self, *keys: str, delay: float = 0.25) -> None:
        if self._irfd is None:
            self._irfd = os.open(self.ir, os.O_RDWR | os.O_NONBLOCK)
        for k in keys:
            os.write(self._irfd, struct.pack("<I", KEYCODES[k]))
            time.sleep(delay)

    def framebuffer(self) -> bytes:
        try:
            return self.display.read_bytes()
        except FileNotFoundError:
            return b""

    def pixels(self) -> list[list[int]]:
        fb = self.framebuffer().ljust(4096, b"\0")
        rows = []
        for y in range(64):
            row = []
            for x in range(128):
                b = fb[y * 64 + x // 2]
                row.append((b >> 4) & 3 if x & 1 else b & 3)
            rows.append(row)
        return rows

    def screenshot(self, path: str, scale: int = 4) -> None:
        from PIL import Image
        colours = [(8, 12, 14), (30, 95, 90), (70, 180, 165), (130, 255, 225)]
        img = Image.new("RGB", (128, 64))
        for y, row in enumerate(self.pixels()):
            for x, v in enumerate(row):
                img.putpixel((x, y), colours[v])
        img.resize((128 * scale, 64 * scale), Image.NEAREST).save(path)

    def audio_bytes(self) -> int:
        try:
            return self.audio.stat().st_size
        except FileNotFoundError:
            return 0

    def console(self) -> str:
        return self.log.read_text(errors="replace")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if self._irfd is not None:
            os.close(self._irfd)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ap.add_argument("server", help="ip:port of the music server")
    ap.add_argument("--keys", default="", help="comma separated key presses")
    ap.add_argument("--wait", type=float, default=3.0, help="seconds to wait after boot")
    ap.add_argument("--shot", help="save a PNG of the display at the end")
    args = ap.parse_args()
    r = Receiver(args.binary, args.server)
    try:
        time.sleep(args.wait)
        if args.keys:
            r.press(*args.keys.split(","), delay=0.6)
            time.sleep(1.0)
        if args.shot:
            r.screenshot(args.shot)
        print(r.console())
    finally:
        r.stop()


if __name__ == "__main__":
    main()
