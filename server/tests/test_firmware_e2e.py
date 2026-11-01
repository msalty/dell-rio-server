"""Boot the real receiver firmware under qemu-arm against a live server.

Skipped unless qemu-arm is installed and the firmware has been built
(cd firmware && make init).
"""

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import threading
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


@contextlib.contextmanager
def run_server(music_dir, data_dir):
    http = free_port()
    env = dict(os.environ, RIO_MUSIC_DIR=str(music_dir), RIO_DATA_DIR=str(data_dir),
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
    try:
        yield f"127.0.0.1:{http}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture()
def server(music_dir, tmp_path):
    with run_server(music_dir, tmp_path / "data") as addr:
        yield addr


@contextlib.contextmanager
def slow_poll_proxy(upstream: str, delay: float):
    """An HTTP/1.0 proxy that answers the receiver's status polls late, as a
    busy server or a lost packet would."""
    host, port = upstream.split(":")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)

    def handle(client):
        with client:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
            if head.startswith(b"GET /ng/poll"):
                time.sleep(delay)
            try:
                up = socket.create_connection((host, int(port)))
            except OSError:
                return  # a late poll from a test whose server has already stopped
            with up:
                up.sendall(head)
                while chunk := up.recv(65536):
                    try:
                        client.sendall(chunk)
                    except OSError:
                        return

    def serve():
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(client,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield f"127.0.0.1:{listener.getsockname()[1]}"
    finally:
        listener.close()


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


@pytest.mark.parametrize("delay", [0.35, 1.0])
def test_slow_server_does_not_interrupt_audio(tmp_path, delay):
    """A slow reply to the receiver's status poll used to stall playback for
    up to its timeout, longer than the audio device's buffer lasts, and a
    reply slower than the timeout lost the commands in it."""
    import json
    import urllib.request

    from simulate import Receiver

    music = tmp_path / "music" / "Artist" / "Album"
    music.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:duration=14:amplitude=0.3", "-ac", "2", "-ar", "44100",
                    "-codec:a", "libmp3lame", "-b:a", "320k", "-metadata", "title=Noise",
                    "-metadata", "artist=Artist", "-metadata", "album=Album", str(music / "01.mp3")],
                   check=True)
    with run_server(tmp_path / "music", tmp_path / "data") as upstream, \
            slow_poll_proxy(upstream, delay) as proxied:
        rx = Receiver(str(FIRMWARE), proxied, workdir=str(tmp_path / "rx"))
        try:
            api = f"http://{upstream}/api"
            deadline = time.time() + 10
            while not json.load(urllib.request.urlopen(f"{api}/receivers")) and time.time() < deadline:
                time.sleep(0.2)
            ip = json.load(urllib.request.urlopen(f"{api}/receivers"))[0]["ip"]
            album = json.load(urllib.request.urlopen(f"{api}/albums"))[0]["id"]
            req = urllib.request.Request(f"{api}/receivers/{ip}/play", data=json.dumps({"album_id": album}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req).read()
            deadline = time.time() + 10
            while "playing" not in rx.console() and time.time() < deadline:
                time.sleep(0.2)
            assert "playing" in rx.console(), rx.console()
            time.sleep(9)  # three regular polls
            log = rx.console()
            assert "audio: underrun" not in log, log
        finally:
            rx.stop()


@contextlib.contextmanager
def stalling_stream_proxy(upstream: str, rate: int, after: int, pause: float):
    """An HTTP/1.0 proxy that sends the first music stream at `rate` bytes a
    second, then stops it for `pause` seconds after `after` bytes, as a slow
    NAS would, and then sends the rest at full speed. It also records the
    status polls.

    The pacing stands in for the real receiver's small TCP window: under
    qemu the firmware uses the host's sockets, whose buffers would otherwise
    take in the whole track at once."""
    host, port = upstream.split(":")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    polls: list[str] = []
    stalled = threading.Event()

    def handle(client):
        with client:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            if line.startswith("GET /ng/poll"):
                polls.append(line)
            stall = line.startswith("GET /content/") and not stalled.is_set()
            if stall:
                stalled.set()
            sent, began = 0, time.monotonic()
            with socket.create_connection((host, int(port))) as up:
                up.sendall(head)
                while chunk := up.recv(8192):
                    if stall:
                        if sent < after <= sent + len(chunk):
                            time.sleep(pause)
                            stall = False  # caught up: the network itself is fast
                        else:
                            ahead = (sent + len(chunk)) / rate - (time.monotonic() - began)
                            if ahead > 0:
                                time.sleep(ahead)
                    sent += len(chunk)
                    try:
                        client.sendall(chunk)
                    except OSError:
                        return

    def serve():
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(client,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield f"127.0.0.1:{listener.getsockname()[1]}", polls
    finally:
        listener.close()


def test_running_out_of_music_is_reported(tmp_path):
    """When the stream stalls for longer than the receiver's buffer lasts,
    the receiver tells the server it ran out, and for how long."""
    import json
    import re
    import urllib.request

    from simulate import Receiver

    music = tmp_path / "music" / "Artist" / "Album"
    music.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:duration=20:amplitude=0.3", "-ac", "2", "-ar", "44100",
                    "-codec:a", "libmp3lame", "-b:a", "320k", "-metadata", "title=Noise",
                    "-metadata", "artist=Artist", "-metadata", "album=Album", str(music / "01.mp3")],
                   check=True)
    # 320 kbps is 40 KB/s. Sent at 45 KB/s, the receiver is about 1.5 s
    # ahead after 5 s of it, so a 4 s stall runs it dry for a few seconds.
    with run_server(tmp_path / "music", tmp_path / "data") as upstream, \
            stalling_stream_proxy(upstream, rate=45 * 1024, after=225 * 1024, pause=4.0) as (proxied, polls):
        rx = Receiver(str(FIRMWARE), proxied, workdir=str(tmp_path / "rx"))
        try:
            api = f"http://{upstream}/api"
            deadline = time.time() + 10
            while not json.load(urllib.request.urlopen(f"{api}/receivers")) and time.time() < deadline:
                time.sleep(0.2)
            rid = json.load(urllib.request.urlopen(f"{api}/receivers"))[0]["id"]
            album = json.load(urllib.request.urlopen(f"{api}/albums"))[0]["id"]
            req = urllib.request.Request(f"{api}/receivers/{rid}/play", data=json.dumps({"album_id": album}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req).read()

            def reports():
                return [(int(m.group(1)), int(m.group(2))) for p in polls
                        if (m := re.search(r"under=(\d+)&underms=(\d+)", p))]

            # Wait until it has run dry and the wait has ended and been reported.
            deadline = time.time() + 25
            while time.time() < deadline:
                r = reports()
                if any(n for n, _ in r) and r[-1] == (0, 0):
                    break
                time.sleep(0.5)
            log = rx.console()
            r = reports()
            assert any(n for n, _ in r), "\n".join(polls[-5:]) + "\n" + log
            # One run-out, counted once, and every millisecond of the wait
            # reported once (a wait can span two reports).
            assert sum(n for n, _ in r) == 1, f"{r}\n{log}"
            assert 1000 <= sum(ms for _, ms in r) <= 6000, f"{r}\n{log}"
            assert "ran out of data" in log
        finally:
            rx.stop()


@contextlib.contextmanager
def slow_menu_proxy(upstream: str, delay: float):
    """An HTTP/1.0 proxy that answers the receiver's menu requests `delay`
    seconds late (once `slow` is set) and records its status polls."""
    host, port = upstream.split(":")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    polls: list[str] = []
    slow = threading.Event()

    def handle(client):
        with client:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            if line.startswith("GET /ng/poll"):
                polls.append(line)
            if line.startswith("GET /ng/menu") and slow.is_set():
                time.sleep(delay)
            try:
                up = socket.create_connection((host, int(port)))
            except OSError:
                return
            with up:
                up.sendall(head)
                while chunk := up.recv(65536):
                    try:
                        client.sendall(chunk)
                    except OSError:
                        return

    def serve():
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(client,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield f"127.0.0.1:{listener.getsockname()[1]}", polls, slow
    finally:
        listener.close()


def test_audio_dropouts_are_reported(tmp_path):
    """The receiver measures how much sound its audio device holds, how busy
    its CPU is while playing, and reports each time the device runs out of
    sound when it shouldn't: here, while a menu loads slowly mid-song."""
    import json
    import re
    import urllib.request

    from simulate import Receiver

    music = tmp_path / "music" / "Artist" / "Album"
    music.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:duration=30:amplitude=0.3", "-ac", "2", "-ar", "44100",
                    "-codec:a", "libmp3lame", "-b:a", "320k", "-metadata", "title=Noise",
                    "-metadata", "artist=Artist", "-metadata", "album=Album", str(music / "01.mp3")],
                   check=True)

    def fields(line):
        return dict(re.findall(r"[?&](\w+)=([^& ]*)", line))

    with run_server(tmp_path / "music", tmp_path / "data") as upstream, \
            slow_menu_proxy(upstream, delay=0.8) as (proxied, polls, slow):
        rx = Receiver(str(FIRMWARE), proxied, workdir=str(tmp_path / "rx"))
        try:
            api = f"http://{upstream}/api"
            deadline = time.time() + 10
            while not json.load(urllib.request.urlopen(f"{api}/receivers")) and time.time() < deadline:
                time.sleep(0.2)
            rid = json.load(urllib.request.urlopen(f"{api}/receivers"))[0]["id"]
            album = json.load(urllib.request.urlopen(f"{api}/albums"))[0]["id"]
            req = urllib.request.Request(f"{api}/receivers/{rid}/play", data=json.dumps({"album_id": album}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req).read()
            deadline = time.time() + 10
            while "playing" not in rx.console() and time.time() < deadline:
                time.sleep(0.2)
            time.sleep(7)  # two polls of steady playback

            steady = [fields(p) for p in polls if fields(p).get("st") == "play"]
            assert steady, polls
            # The simulated device holds about 0.3 s, like the real one is thought to.
            assert all(f["gaps"] == "0" for f in steady), steady
            buffers = {int(f["abuf"]) for f in steady if f["abuf"] != "0"}
            assert buffers and all(200 <= b <= 400 for b in buffers), steady
            assert any(0 <= int(f["cpu"]) <= 100 for f in steady), steady
            # where the time went: decoding, the display, the network
            assert any(all(0 <= int(f[k]) <= 100 for k in ("dec", "disp", "net")) for f in steady), steady

            # Now Playing > back to the menus > down past "Now Playing" > open
            # "Music": its menu takes 0.8 s to arrive, and playback waits for it.
            rx.press("menu", "down", delay=0.4)
            seen = len(polls)
            slow.set()
            rx.press("select", delay=0.4)
            deadline = time.time() + 10
            while not any(int(fields(p).get("gaps", 0)) for p in polls[seen:]) and time.time() < deadline:
                time.sleep(0.3)
            log = rx.console()
            reported = [fields(p) for p in polls[seen:] if int(fields(p).get("gaps", 0))]
            assert reported, "\n".join(polls[seen:]) + "\n" + log
            assert sum(int(f["gaps"]) for f in reported) == 1, reported
            # 0.8 s held up, less the ~0.3 s the device had in hand
            assert 250 <= sum(int(f["gapms"]) for f in reported) <= 900, reported
            assert "audio: dropout" in log
        finally:
            rx.stop()


def test_now_playing_keeps_scrolling_a_long_title(tmp_path):
    """Now Playing scrolls a long title for as long as it plays. Each step
    redraws only the title line (a full redraw costs CPU that decoding a
    320 kbps track needs), so the lines below must stay intact."""
    import json
    import urllib.request

    from simulate import Receiver

    music = tmp_path / "music" / "Artist" / "Album"
    music.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=40", "-ac", "2", "-ar", "44100",
                    "-codec:a", "libmp3lame", "-b:a", "128k", "-metadata", "title=A Title Long Enough To Scroll",
                    "-metadata", "artist=Artist", "-metadata", "album=Album", str(music / "01.mp3")],
                   check=True)
    with run_server(tmp_path / "music", tmp_path / "data") as server:
        rx = Receiver(str(FIRMWARE), server, workdir=str(tmp_path / "rx"))
        try:
            api = f"http://{server}/api"
            deadline = time.time() + 10
            while not json.load(urllib.request.urlopen(f"{api}/receivers")) and time.time() < deadline:
                time.sleep(0.2)
            rid = json.load(urllib.request.urlopen(f"{api}/receivers"))[0]["id"]
            album = json.load(urllib.request.urlopen(f"{api}/albums"))[0]["id"]
            req = urllib.request.Request(f"{api}/receivers/{rid}/play", data=json.dumps({"album_id": album}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req).read()
            deadline = time.time() + 10
            while "playing" not in rx.console() and time.time() < deadline:
                time.sleep(0.2)
            time.sleep(0.5)

            def rows(a, b):
                return [tuple(row) for row in rx.pixels()[a:b]]

            at_start = rows(12, 29)  # the title line, before it starts to move
            below = rows(29, 52)     # artist and album
            assert lit_pixels(at_start) > 50 and lit_pixels(below) > 50, rx.console()
            # One pass of this title takes about 9 s; watch for 25, so it has to
            # start over at least once rather than settle.
            seen, moving_at = set(), []
            began = time.time()
            while time.time() - began < 25:
                time.sleep(0.5)
                title = rows(12, 29)
                assert lit_pixels(title) > 50, "the title line was cleared and not redrawn"
                assert rows(29, 52) == below, "a scroll step disturbed the artist and album lines"
                seen.add(tuple(title))
                if title != at_start:
                    moving_at.append(time.time() - began)
            assert len(seen) > 10, "the title barely moved"
            assert moving_at and moving_at[-1] > 20, "the title stopped scrolling"
        finally:
            rx.stop()
