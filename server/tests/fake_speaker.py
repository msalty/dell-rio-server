"""Stand-ins for shairport-sync and librespot in the tests.

    fake_speaker.py shairport -c <config>
    fake_speaker.py librespot --name <name> ... --onevent <program> ...

Each writes PCM (a tone) to stdout while "playing", and reports what the real
program would: shairport-sync's metadata items on its metadata pipe and its
advertisement through `avahi-publish-service` (found on PATH), librespot's
events through its --onevent program. The test drives it by writing JSON
lines to a FIFO at $FAKE_SPEAKER_DIR/<RIO_HOOK_INSTANCE>.ctl:

    {"play": <seconds>, "title": .., "artist": .., "album": .., "sender": ..}
    {"pause": true} / {"resume": true} / {"stop": true} / {"volume": <n>}

It appends "start" (with its arguments) to $FAKE_SPEAKER_DIR/<instance>.log
each time it starts, so tests can see restarts.
"""

import base64
import json
import math
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time

RATE = 44100


def tone(frames: int, start: int) -> bytes:
    import array

    a = array.array("h")
    for i in range(start, start + frames):
        v = int(6000 * math.sin(2 * math.pi * 440 * i / RATE))
        a.extend((v, v))
    return a.tobytes()


class Fake:
    def __init__(self, kind, args):
        self.kind = kind
        self.args = args
        self.dir = os.environ["FAKE_SPEAKER_DIR"]
        self.instance = os.environ["RIO_HOOK_INSTANCE"]
        with open(os.path.join(self.dir, self.instance + ".log"), "a") as f:
            f.write("start " + json.dumps(args) + "\n")
        self.playing_until = 0.0
        self.paused = False
        self.frames = 0
        self.lock = threading.Lock()
        self.publisher = None
        self.meta_fd = None
        if kind == "shairport":
            conf = open(args[args.index("-c") + 1]).read()
            self.name = re.search(r'name = "((?:[^"\\]|\\.)*)"', conf)[1]
            self.port = int(re.search(r"\bport = (\d+)", conf)[1])
            self.pipe = re.search(r'pipe_name = "([^"]+)"', conf)[1]
            self.meta_fd = os.open(self.pipe, os.O_WRONLY)
            self.publisher = subprocess.Popen(["avahi-publish-service", f"ABCDEF012345@{self.name}", "_raop._tcp",
                                               str(self.port), "txtvers=1", "tp=UDP", "am=ShairportSync"])
        else:
            self.name = args[args.index("--name") + 1]
            self.onevent = args[args.index("--onevent") + 1]

    # -- reporting, as the real programs do --------------------------------
    def item(self, kind: str, code: str, data: bytes = b"") -> None:
        text = (f"<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code>"
                f"<length>{len(data)}</length>")
        if data:
            text += f'\n<data encoding="base64">\n{base64.b64encode(data).decode()}</data>'
        os.write(self.meta_fd, (text + "</item>\n").encode())

    def event(self, **env) -> None:
        subprocess.run([self.onevent], env=dict(os.environ, **{k: str(v) for k, v in env.items()}), check=False)

    def command(self, cmd: dict) -> None:
        if "play" in cmd:
            if self.kind == "shairport":
                self.item("ssnc", "snam", cmd.get("sender", "iPhone").encode())
                self.item("ssnc", "pbeg")
                self.item("ssnc", "mdst")
                self.item("core", "minm", cmd.get("title", "").encode())
                self.item("core", "asar", cmd.get("artist", "").encode())
                self.item("core", "asal", cmd.get("album", "").encode())
                self.item("ssnc", "mden")
                self.item("ssnc", "PICT", b"\xff\xd8\xff\xe0fake jpeg")
                self.item("ssnc", "pvol", b"-15.00,-20.00,-96.30,0.00")
            else:
                self.event(PLAYER_EVENT="session_connected", USER_NAME="someone", CONNECTION_ID="1")
                self.event(PLAYER_EVENT="session_client_changed", CLIENT_NAME=cmd.get("sender", "Pixel"),
                           CLIENT_ID="x", CLIENT_BRAND_NAME="google", CLIENT_MODEL_NAME="Pixel")
                self.event(PLAYER_EVENT="track_changed", NAME=cmd.get("title", ""), ARTISTS=cmd.get("artist", ""),
                           ALBUM=cmd.get("album", ""), COVERS="https://i.scdn.co/image/abc\nhttps://i.scdn.co/image/def",
                           TRACK_ID="t1", DURATION_MS="200000", ITEM_TYPE="Track")
                self.event(PLAYER_EVENT="playing", TRACK_ID="t1", POSITION_MS="0")
            with self.lock:
                self.playing_until = time.monotonic() + float(cmd["play"])
                self.paused = False
        elif cmd.get("pause"):
            self.paused = True
            if self.kind == "shairport":
                self.item("ssnc", "pfls")
            else:
                self.event(PLAYER_EVENT="paused", TRACK_ID="t1", POSITION_MS="1000")
        elif cmd.get("resume"):
            self.paused = False
            if self.kind == "shairport":
                self.item("ssnc", "prsm")
            else:
                self.event(PLAYER_EVENT="playing", TRACK_ID="t1", POSITION_MS="1000")
        elif cmd.get("stop"):
            self.playing_until = 0
            if self.kind == "shairport":
                self.item("ssnc", "pend")
            else:
                self.event(PLAYER_EVENT="stopped", TRACK_ID="t1")
                self.event(PLAYER_EVENT="session_disconnected", USER_NAME="someone", CONNECTION_ID="1")
        elif "volume" in cmd:
            if self.kind == "shairport":
                self.item("ssnc", "pvol", f"{cmd['volume']:.2f},0.00,0.00,0.00".encode())
            else:
                self.event(PLAYER_EVENT="volume_changed", VOLUME=int(cmd["volume"]))

    # -- audio ----------------------------------------------------------------
    def audio(self) -> None:
        """shairport-sync writes in real time; librespot as fast as the pipe takes it."""
        out = sys.stdout.buffer
        while True:
            if time.monotonic() < self.playing_until and not self.paused:
                out.write(tone(352, self.frames))
                out.flush()
                self.frames += 352
                if self.kind == "shairport":
                    time.sleep(352 / RATE)
            else:
                time.sleep(0.02)

    def run(self) -> None:
        threading.Thread(target=self.audio, daemon=True).start()
        ctl = os.path.join(self.dir, self.instance + ".ctl")
        if not os.path.exists(ctl):
            os.mkfifo(ctl)
        fd = os.open(ctl, os.O_RDWR | os.O_NONBLOCK)
        buf = b""
        while True:
            select.select([fd], [], [], 1.0)
            try:
                buf += os.read(fd, 65536)
            except BlockingIOError:
                pass
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self.command(json.loads(line))

    def stop(self, *_):
        if self.publisher is not None:
            self.publisher.terminate()  # as shairport-sync withdraws its advertisement
            self.publisher.wait()
        os._exit(0)


def main():
    fake = Fake(sys.argv[1], sys.argv[2:])
    signal.signal(signal.SIGTERM, fake.stop)
    fake.run()


if __name__ == "__main__":
    main()
