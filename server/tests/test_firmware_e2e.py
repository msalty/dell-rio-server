"""Boot the real receiver firmware under qemu-arm against a live server.

Skipped unless qemu-arm is installed and the firmware has been built
(cd firmware && make init).
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIRMWARE = ROOT / "firmware" / "build" / "init"
sys.path.insert(0, str(ROOT / "firmware" / "tools"))

pytestmark = pytest.mark.skipif(
    not shutil.which("qemu-arm") or not FIRMWARE.exists(),
    reason="needs qemu-arm and a firmware build (make -C firmware init)",
)


def free_port(kind=socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def server(music_dir, tmp_path):
    http = free_port()
    env = dict(os.environ, RIO_MUSIC_DIR=str(music_dir), RIO_DATA_DIR=str(tmp_path / "data"),
               RIO_HTTP_PORT=str(http), RIO_WEB_PORT="0", RIO_ENABLE_BOOT="0",
               RIO_SSDP_PORT=str(free_port(socket.SOCK_DGRAM)), RIO_WATCH="0")
    proc = subprocess.Popen([sys.executable, "-m", "rioserver"], cwd=ROOT / "server", env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", http), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.2)
    time.sleep(1.0)  # initial scan
    yield f"127.0.0.1:{http}"
    proc.terminate()
    proc.wait(timeout=10)


def lit_pixels(rows) -> int:
    return sum(1 for row in rows for v in row if v)


def test_browse_artist_album_and_play(server, tmp_path):
    from simulate import Receiver

    rx = Receiver(str(FIRMWARE), server, workdir=str(tmp_path / "rx"))
    try:
        time.sleep(3)
        root = rx.pixels()
        assert lit_pixels(root) > 300, rx.console()
        # Music > Artists > The Beatles > Abbey Road > "Something"
        rx.press("select", "select", "down", "down", "select", "down", delay=0.5)
        before = rx.audio_bytes()
        rx.press("select", delay=0.5)
        time.sleep(3)
        log = rx.console()
        assert 'playing' in log and '"Something"' in log, log
        assert rx.audio_bytes() > before + 100_000  # decoded PCM reached the "device"
        # playback continues with the next album track, not the next title alphabetically
        deadline = time.time() + 10
        while '"Maxwell\'s Silver Hammer"' not in rx.console() and time.time() < deadline:
            time.sleep(0.5)
        assert '"Maxwell\'s Silver Hammer"' in rx.console()
    finally:
        rx.stop()


def test_web_commands_take_effect_immediately(server, tmp_path):
    """The server wakes the receiver over UDP instead of waiting for its
    next poll (every 3 s), so remote control feels instant."""
    import json
    import urllib.request

    from simulate import Receiver

    rx = Receiver(str(FIRMWARE), server, workdir=str(tmp_path / "rx"))
    try:
        time.sleep(3)
        ip = json.load(urllib.request.urlopen(f"http://{server}/api/receivers"))[0]["ip"]
        for i in range(3):
            before = rx.framebuffer()
            t0 = time.time()
            req = urllib.request.Request(f"http://{server}/api/receivers/{ip}/command",
                                         data=json.dumps({"cmd": "msg", "value": f"Hello {i}"}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req).read()
            while rx.framebuffer() == before and time.time() - t0 < 5:
                time.sleep(0.01)
            assert time.time() - t0 < 1.0, rx.console()
            time.sleep(0.8)
    finally:
        rx.stop()
