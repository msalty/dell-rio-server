"""Receivers as AirPlay and Spotify Connect speakers.

For each NG receiver (firmware 1.8 or later) with the switches on in the web
interface, the server runs a shairport-sync (classic AirPlay) and/or a
librespot (Spotify Connect) named after the receiver. Their PCM output goes
into the receiver's LiveStream (live.py), and the receiver plays the MP3
stream from /live/<id> (the firmware's live mode, "cmd live").

Sessions: when a phone starts playing to a receiver's speaker, the server
saves the receiver's queue (for "Resume Up Next" in the web interface),
switches the receiver to the live stream and sends now-playing details as
"cmd meta". The newest sender wins: a Spotify session takes over from an
AirPlay one on the same receiver (and the AirPlay sender is disconnected),
and the other way round. Music started on the receiver itself, or from the
web interface, ends the live session, as does Stop; the phone is
disconnected by restarting that process, the one way to stop it that both
programs offer.

Processes:
- shairport-sync writes PCM to stdout and metadata to a pipe (shairport
  "ssnc"/"core" items). It advertises itself by running avahi-publish-service,
  which here is the server's own stand-in (hook.py): the server publishes
  _raop._tcp on its shared Zeroconf instance under rioserver.local, with
  shairport-sync's own TXT records.
- librespot writes PCM to stdout (pipe backend) and runs hook.py for its
  events. It answers mDNS itself (libmdns), restricted to the LAN address
  with --zeroconf-interface, since otherwise it would answer for the host's
  name with every address the host has, Docker bridges included. Zeroconf
  login means the phone's Spotify app authorises it: no credentials are
  stored here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

from aiohttp import web

from .live import LiveStream
from .receivers import Receiver, firmware_version

log = logging.getLogger(__name__)

LIVE_FIRMWARE = (1, 8)  # NG firmware with the live mode
KINDS = ("airplay", "spotify")
APP_NAMES = {"airplay": "AirPlay", "spotify": "Spotify"}
RECONCILE_SECONDS = 3.0
OFFLINE_GRACE = 300  # seconds a receiver may be silent before its speakers are withdrawn
CONFIRM_SECONDS = 15  # time a receiver has to report it plays the live stream
PAUSED_END = 600  # seconds paused before the session ends and the receiver stops
RESTART_DELAYS = (2, 5, 15, 30, 60)  # after a process exits unexpectedly
META_SETTLE = 0.25  # seconds to gather metadata changes into one "meta" command
AIRPLAY_PORT_STRIDE = 16
NAME_LIMIT = 40


def stream_id(r: Receiver) -> str:
    return re.sub(r"[^0-9a-f]", "", (r.mac or r.ip).lower()) or "0"


def _conf_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_items(buf: bytes) -> tuple[list[tuple[str, str, bytes]], bytes]:
    """Complete shairport-sync metadata items in `buf`, and what's left over.
    Each item is (type, code, data), e.g. ("core", "minm", b"Title")."""
    items = []
    while True:
        end = buf.find(b"</item>")
        if end < 0:
            return items, buf[-1_000_000:]
        chunk, buf = buf[:end], buf[end + 7:]
        m = re.search(rb"<type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code><length>(\d+)</length>", chunk)
        if not m:
            continue
        try:
            kind, code = bytes.fromhex(m[1].decode()).decode("latin-1"), bytes.fromhex(m[2].decode()).decode("latin-1")
        except ValueError:
            continue
        data = b""
        d = re.search(rb'<data encoding="base64">\s*([A-Za-z0-9+/=\s]*)</data>', chunk)
        if d:
            try:
                data = base64.b64decode(re.sub(rb"\s", b"", d[1]))
            except ValueError:
                data = b""
        items.append((kind, code, data))


def airplay_volume(value: str) -> int | None:
    """shairport-sync's pvol ("airplay_volume,..."; -30..0, -144 is mute) as 0-100."""
    try:
        v = float(value.split(",")[0])
    except (ValueError, IndexError):
        return None
    if v <= -30:
        return 0
    return max(0, min(100, round((v + 30) * 100 / 30)))


def _image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    return "image/jpeg"


class Session:
    def __init__(self, kind: str):
        self.kind = kind
        self.started = time.monotonic()
        self.confirmed = False
        self.paused_at: float | None = None


class Instance:
    """One shairport-sync or librespot process for one receiver."""

    kind = ""

    def __init__(self, mgr: "Speakers", key: str, name: str, slot: int, ip: str):
        self.mgr = mgr
        self.key = key  # the receiver's
        self.name = name
        self.slot = slot
        self.ip = ip
        self.id = f"{self.kind}-{slot}"
        self.proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._runner: asyncio.Task | None = None
        self._stopping = False
        self._restart_now = asyncio.Event()
        self.last_error = ""

    def command(self) -> list[str]:
        raise NotImplementedError

    def env(self) -> dict:
        env = dict(os.environ)
        env["RIO_HOOK_SOCKET"] = str(self.mgr.hook_path)
        env["RIO_HOOK_INSTANCE"] = self.id
        env["PATH"] = f"{self.mgr.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return env

    def start(self) -> None:
        self._stopping = False
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        await self._kill()
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except (asyncio.CancelledError, Exception):
                pass
            self._runner = None
        self.cleanup()

    def restart(self) -> None:
        """Stop the process and start it again at once (disconnects the sender)."""
        self._restart_now.set()
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    def cleanup(self) -> None:
        pass

    async def prepare(self) -> None:
        pass

    async def _kill(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), 5)
        except ProcessLookupError:
            pass
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def _run(self) -> None:
        failures = 0
        while not self._stopping:
            self._restart_now.clear()
            began = time.monotonic()
            try:
                await self.prepare()
                self.proc = await asyncio.create_subprocess_exec(
                    *self.command(), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=self.env(), start_new_session=True)
            except OSError as exc:
                self.last_error = str(exc)
                log.error("%s: could not start %s: %s", self.name, self.kind, exc)
                await asyncio.sleep(RESTART_DELAYS[-1])
                continue
            log.info("%s: %s speaker started (pid %d)", self.name, APP_NAMES[self.kind], self.proc.pid)
            tail: list[str] = []
            readers = [asyncio.create_task(self._pcm(self.proc.stdout)),
                       asyncio.create_task(self._log(self.proc.stderr, tail))]
            await self.proc.wait()
            for t in readers:
                t.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            self.mgr.instance_exited(self)
            if self._stopping:
                return
            if self._restart_now.is_set():
                failures = 0
                continue
            failures = failures + 1 if time.monotonic() - began < 60 else 1
            self.last_error = tail[-1] if tail else f"exit code {self.proc.returncode}"
            delay = RESTART_DELAYS[min(failures, len(RESTART_DELAYS)) - 1]
            log.warning("%s: %s stopped (%s); restarting in %d s%s", self.name, self.kind, self.last_error, delay,
                        "".join("\n    " + line for line in tail[-5:]))
            try:
                await asyncio.wait_for(self._restart_now.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def _pcm(self, reader: asyncio.StreamReader) -> None:
        """Audio from the process: into the receiver's stream while this is
        the receiver's session, as fast as the stream plays it."""
        while True:
            stream = self.mgr.stream_for(self)
            if stream is not None and stream.room() < 16384:
                await asyncio.sleep(0.04)
                continue
            chunk = await reader.read(16384)
            if not chunk:
                return
            if stream is not None:
                stream.feed(chunk)

    async def _log(self, reader: asyncio.StreamReader, tail: list[str]) -> None:
        while True:
            line = await reader.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                tail.append(text)
                del tail[:-20]
                log.debug("%s %s: %s", self.name, self.kind, text)


class AirPlayInstance(Instance):
    kind = "airplay"

    @property
    def port(self) -> int:
        return self.mgr.cfg.airplay_port_base + AIRPLAY_PORT_STRIDE * self.slot

    @property
    def conf_path(self) -> Path:
        return self.mgr.run_dir / f"{self.id}.conf"

    @property
    def pipe_path(self) -> Path:
        return self.mgr.run_dir / f"{self.id}.meta"

    def command(self) -> list[str]:
        return [self.mgr.shairport_bin, "-c", str(self.conf_path)]

    async def prepare(self) -> None:
        self.conf_path.write_text(
            "general = {\n"
            f"  name = {_conf_string(self.name)};\n"
            f"  port = {self.port};\n"
            f"  udp_port_base = {self.port + 1};\n"
            "  udp_port_range = 10;\n"
            '  output_backend = "stdout";\n'
            '  mdns_backend = "external-avahi";\n'
            '  ignore_volume_control = "yes";\n'  # the receiver sets the volume
            "};\n"
            "sessioncontrol = {\n"
            '  allow_session_interruption = "yes";\n'  # the newest sender wins
            "  session_timeout = 20;\n"
            "};\n"
            "metadata = {\n"
            '  enabled = "yes";\n'
            '  include_cover_art = "yes";\n'
            f"  pipe_name = {_conf_string(str(self.pipe_path))};\n"
            '  cover_art_cache_directory = "";\n'
            "};\n")
        if not self.pipe_path.exists():
            os.mkfifo(self.pipe_path)
        if not self._tasks:
            self._tasks.append(asyncio.create_task(self._metadata()))

    async def _metadata(self) -> None:
        """Read the metadata pipe. It is held open for writing too, so it
        never reaches end-of-file when shairport-sync restarts."""
        loop = asyncio.get_running_loop()
        rfd = os.open(self.pipe_path, os.O_RDONLY | os.O_NONBLOCK)
        wfd = os.open(self.pipe_path, os.O_WRONLY | os.O_NONBLOCK)
        ready = asyncio.Event()
        loop.add_reader(rfd, ready.set)
        buf = b""
        try:
            while True:
                await ready.wait()
                ready.clear()
                while True:
                    try:
                        data = os.read(rfd, 65536)
                    except BlockingIOError:
                        break
                    if not data:
                        break
                    buf += data
                items, buf = parse_items(buf)
                for kind, code, value in items:
                    try:
                        self.mgr.airplay_item(self, kind, code, value)
                    except Exception:
                        log.exception("AirPlay metadata %s/%s", kind, code)
        finally:
            loop.remove_reader(rfd)
            os.close(rfd)
            os.close(wfd)

    def cleanup(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        for p in (self.conf_path, self.pipe_path):
            try:
                p.unlink()
            except OSError:
                pass


class SpotifyInstance(Instance):
    kind = "spotify"

    @property
    def port(self) -> int:
        return self.mgr.cfg.spotify_port_base + self.slot

    def command(self) -> list[str]:
        return [self.mgr.librespot_bin, "--name", self.name, "--device-type", "speaker",
                "--backend", "pipe", "--format", "S16", "--bitrate", "320",
                "--volume-ctrl", "fixed", "--disable-audio-cache",
                "--onevent", str(self.mgr.bin_dir / "rio-event"),
                "--zeroconf-port", str(self.port), "--zeroconf-interface", self.ip]


class Speakers:
    def __init__(self, cfg, registry, shared_zeroconf=None):
        self.cfg = cfg
        self.registry = registry
        self.shared = shared_zeroconf
        self.settings_path = cfg.data_dir / "speakers.json"
        self.settings: dict[str, dict] = {}
        try:
            self.settings = {str(k): dict(v) for k, v in json.loads(self.settings_path.read_text()).items()}
        except (OSError, ValueError, AttributeError):
            pass
        self.shairport_bin = shutil.which(cfg.shairport_bin) if cfg.airplay else None
        self.librespot_bin = shutil.which(cfg.librespot_bin) if cfg.spotify else None
        self.instances: dict[tuple[str, str], Instance] = {}  # (receiver key, kind)
        self.sessions: dict[str, Session] = {}  # receiver key ->
        self.streams: dict[str, LiveStream] = {}
        self.saved: dict[str, dict] = {}  # receiver key -> the queue a session replaced
        self.published: dict[tuple[str, str], object] = {}  # (instance id, name) -> ServiceInfo
        self.run_dir = Path(tempfile.mkdtemp(prefix="rioserver-"))
        self.bin_dir = self.run_dir / "bin"
        self.hook_path = self.run_dir / "hook.sock"
        self._transport = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._meta_due: dict[str, asyncio.TimerHandle] = {}
        self._ids: dict[str, str] = {}  # stream id -> receiver key
        # (receiver key, kind) -> what that speaker last said it plays (it can
        # say so before its session starts, or while another one has the receiver)
        self._known: dict[tuple[str, str], dict] = {}
        self._ended: dict[str, float] = {}  # receiver key -> when its last session ended
        registry.live_hook = self.local_command

    # -- availability and settings -----------------------------------------
    def available(self) -> dict:
        return {"airplay": bool(self.shairport_bin), "spotify": bool(self.librespot_bin)}

    def view(self, r: Receiver) -> dict:
        s = self.settings.get(r.key, {})
        ver = firmware_version(r.state.get("ver"))
        return {"airplay": bool(s.get("airplay")), "spotify": bool(s.get("spotify")),
                "available": self.available(),
                "needs_update": r.firmware == "ng" and bool(ver) and ver < LIVE_FIRMWARE}

    def refresh(self, r: Receiver) -> None:
        """Update what the web interface shows of r's speakers."""
        if r.firmware == "ng":
            r.speakers = self.view(r)
        r.resumable = r.key in self.saved

    def configure(self, r: Receiver, **switches) -> None:
        s = self.settings.setdefault(r.key, {})
        for kind in KINDS:
            if kind in switches:
                s[kind] = bool(switches[kind])
        if "slot" not in s and any(s.get(k) for k in KINDS):
            used = {v.get("slot") for v in self.settings.values()}
            s["slot"] = next(n for n in range(1000) if n not in used)
        self._save()
        self.refresh(r)
        self._wake.set()

    def _save(self) -> None:
        try:
            self.settings_path.write_text(json.dumps(self.settings, indent=1, sort_keys=True))
        except OSError as exc:
            log.warning("could not save speaker settings: %s", exc)

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        package_root = Path(__file__).resolve().parent.parent
        for name, mode in (("avahi-publish-service", "publish"), ("rio-event", "event")):
            p = self.bin_dir / name
            p.write_text(f"#!/bin/sh\nPYTHONPATH={_sh(str(package_root))}${{PYTHONPATH:+:$PYTHONPATH}} "
                         f"exec {_sh(sys.executable)} -m rioserver.hook {mode} \"$@\"\n")
            p.chmod(0o755)
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _HookProtocol(self), local_addr=str(self.hook_path), family=socket.AF_UNIX)
        self._task = asyncio.create_task(self._reconcile_loop())
        missing = [n for n, ok in (("shairport-sync", self.shairport_bin or not self.cfg.airplay),
                                   ("librespot", self.librespot_bin or not self.cfg.spotify)) if not ok]
        if missing:
            log.info("AirPlay/Spotify Connect: %s not installed; those speakers are unavailable", " and ".join(missing))

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        for inst in list(self.instances.values()):
            await inst.stop()
        self.instances.clear()
        for s in self.streams.values():
            await s.stop()
        for key in list(self.published):
            await self._withdraw(key)
        if self._transport is not None:
            self._transport.close()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def poke(self) -> None:
        """Something changed (a receiver appeared, was renamed): look again."""
        self._wake.set()

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await self.reconcile()
            except Exception:
                log.exception("AirPlay/Spotify speakers")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), RECONCILE_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _wanted(self) -> dict[tuple[str, str], tuple[str, int, str]]:
        """(receiver key, kind) -> (name, slot, LAN address) for what should run."""
        from .ssdp import local_ip_for

        avail = self.available()
        want = {}
        now = time.time()
        for r in self.registry.all():
            if not isinstance(r, Receiver) or r.firmware != "ng":
                continue
            s = self.settings.get(r.key)
            if not s or firmware_version(r.state.get("ver")) < LIVE_FIRMWARE:
                continue
            if now - r.last_seen > OFFLINE_GRACE:
                continue
            ip = self.cfg.server_ip or local_ip_for(r.ip)
            for kind in KINDS:
                if s.get(kind) and avail[kind]:
                    want[(r.key, kind)] = (r.name[:NAME_LIMIT], int(s.get("slot", 0)), ip)
        return want

    async def reconcile(self) -> None:
        for r in self.registry.all():
            if isinstance(r, Receiver):
                self.refresh(r)
        want = self._wanted()
        for key, inst in list(self.instances.items()):
            spec = want.get(key)
            if spec is None or spec != (inst.name, inst.slot, inst.ip):
                why = "renamed" if spec else "switched off or gone"
                log.info("%s: %s speaker %s", inst.name, APP_NAMES[inst.kind], why)
                del self.instances[key]
                if self._session_kind(key[0]) == inst.kind:
                    self.end(key[0], kick=False)
                await inst.stop()
                for pub in [k for k in self.published if k[0] == inst.id]:
                    await self._withdraw(pub)
        for key, (name, slot, ip) in want.items():
            if key not in self.instances:
                cls = AirPlayInstance if key[1] == "airplay" else SpotifyInstance
                inst = cls(self, key[0], name, slot, ip)
                self.instances[key] = inst
                inst.start()
        # Paused for a long time: let the receiver go idle.
        for rkey, sess in list(self.sessions.items()):
            if sess.paused_at is not None and time.monotonic() - sess.paused_at > PAUSED_END:
                log.info("live session on %s paused for %d min; ending it", rkey, PAUSED_END // 60)
                self.end(rkey, kick=False)

    def instance_exited(self, inst: Instance) -> None:
        for key in [k for k in self.published if k[0] == inst.id]:
            asyncio.ensure_future(self._withdraw(key))
        if self._session_kind(inst.key) == inst.kind:
            self.end(inst.key, kick=False)

    # -- helpers --------------------------------------------------------------
    def _receiver(self, key: str) -> Receiver | None:
        r = self.registry.get(key)
        return r if isinstance(r, Receiver) else None

    def _instance(self, instance_id: str) -> Instance | None:
        return next((i for i in self.instances.values() if i.id == instance_id), None)

    def _session_kind(self, key: str) -> str | None:
        s = self.sessions.get(key)
        return s.kind if s else None

    def stream(self, key: str) -> LiveStream:
        st = self.streams.get(key)
        if st is None:
            st = self.streams[key] = LiveStream(key, self.cfg.live_bitrate)
            st.on_change.append(lambda changed, key=key: self._meta_changed(key, changed))
        return st

    def stream_for(self, inst: Instance) -> LiveStream | None:
        """The stream an instance's audio goes to, if it has the session."""
        if self._session_kind(inst.key) != inst.kind:
            return None
        st = self.streams.get(inst.key)
        return st if st is not None and st.running else None

    # -- sessions -------------------------------------------------------------
    def begin(self, key: str, kind: str) -> None:
        r = self._receiver(key)
        if r is None:
            return
        old = self.sessions.get(key)
        if old is not None and old.kind == kind:
            if old.paused_at is not None:
                old.paused_at = None
                self.stream(key).set_meta(paused=False)
            return
        st = self.stream(key)
        if old is not None:
            # The newest sender wins: disconnect the other one, keep streaming.
            log.info("%s: %s takes over from %s", r.name, APP_NAMES[kind], APP_NAMES[old.kind])
            other = self.instances.get((key, old.kind))
            if other is not None:
                other.restart()
            self.sessions[key] = Session(kind)
            self.sessions[key].confirmed = old.confirmed
            st.clear_meta()
            st.set_meta(source=kind, **self._known.get((key, kind), {}))
            self._send_meta(key)
            return
        log.info("%s: %s session starts", r.name, APP_NAMES[kind])
        ids, index, pos, _ = self.registry.now_playing(r)
        if ids:
            self.saved[key] = {"track_ids": ids, "index": index, "pos_ms": pos}
            r.resumable = True
        r.pq.load([])
        r.queue_known = False
        self.sessions[key] = Session(kind)
        sid = stream_id(r)
        self._ids[sid] = key
        st.clear_meta()
        st.set_meta(source=kind, **self._known.get((key, kind), {}))

        async def go():
            try:
                await st.start()
            except Exception as exc:
                log.error("%s: could not start the live stream: %s", r.name, exc)
                self.sessions.pop(key, None)
                return
            if self._session_kind(key) is None:
                await st.stop()
                return
            self.registry.command(r, "live", f"/live/{sid}", self._label(key))
            self._send_meta(key)
        asyncio.ensure_future(go())

    def end(self, key: str, kick: bool, stop_receiver: bool = True) -> None:
        """End the live session on a receiver: stop the stream (and the
        receiver), and with `kick` disconnect the sender."""
        sess = self.sessions.pop(key, None)
        if sess is None:
            return
        self._ended[key] = time.monotonic()
        r = self._receiver(key)
        log.info("%s: %s session ends", r.name if r else key, APP_NAMES[sess.kind])
        st = self.streams.get(key)
        if st is not None:
            st.clear_meta()
            asyncio.ensure_future(st.stop())
        handle = self._meta_due.pop(key, None)
        if handle:
            handle.cancel()
        if kick:
            inst = self.instances.get((key, sess.kind))
            if inst is not None:
                inst.restart()
        if r is not None:
            r.live = None
            if stop_receiver:
                self.registry.command(r, "stop")

    def local_command(self, r: Receiver, verb: str) -> bool:
        """A command for receiver r from the web interface (registry.command).
        Returns True if it was dealt with here and must not be sent."""
        if verb in ("play", "queue") and self.saved.pop(r.key, None) is not None:
            r.resumable = False  # a new queue: nothing to resume
        if r.key not in self.sessions:
            return False
        if verb in ("play", "queue"):
            self.end(r.key, kick=True, stop_receiver=False)
            return False
        if verb in ("stop", "pause", "toggle"):
            self.end(r.key, kick=True)
            return True
        return verb in ("next", "prev", "resume", "seek", "repeat")  # nothing to do while live

    def polled(self, r: Receiver, live: str) -> None:
        """A status poll: is the receiver playing the live stream?"""
        self.refresh(r)
        sess = self.sessions.get(r.key)
        if sess is None:
            # Still on a stream whose session is over (the server restarted,
            # say). Just after a session ends, the stop is on its way.
            if live == "1" and r.state.get("st") in ("play", "buffer") \
                    and time.monotonic() - self._ended.get(r.key, float("-inf")) > CONFIRM_SECONDS:
                self._ended[r.key] = time.monotonic()
                self.registry.command(r, "stop")
            return
        if live == "1":
            sess.confirmed = True
            return
        if sess.confirmed or time.monotonic() - sess.started > CONFIRM_SECONDS:
            # Stopped, or playing something else, on the receiver itself.
            log.info("%s: playback changed on the receiver; ending the %s session", r.name, APP_NAMES[sess.kind])
            self.end(r.key, kick=True, stop_receiver=False)

    def resume(self, r: Receiver) -> dict | None:
        saved = self.saved.pop(r.key, None)
        r.resumable = False
        return saved

    # -- metadata ---------------------------------------------------------------
    def _label(self, key: str) -> str:
        st = self.streams.get(key)
        meta = st.meta if st else {}
        app = APP_NAMES.get(meta.get("source") or self._session_kind(key) or "", "")
        return f"{app} · {meta['sender']}" if meta.get("sender") else app

    def _meta_changed(self, key: str, changed: dict) -> None:
        r = self._receiver(key)
        if r is None or key not in self.sessions:
            return
        st = self.streams[key]
        m = st.meta
        art = None
        if m.get("art"):
            art = f"/api/receivers/{r.id}/live/art?v={m.get('art_version', 0)}"
        elif m.get("art_url"):
            art = m["art_url"]
        r.live = {"source": m.get("source") or self._session_kind(key), "app": APP_NAMES.get(m.get("source") or "", ""),
                  "sender": m.get("sender", ""), "title": m.get("title", ""), "artist": m.get("artist", ""),
                  "album": m.get("album", ""), "art": art, "paused": bool(m.get("paused")),
                  "volume": m.get("volume")}
        if {"title", "artist", "album", "sender", "source"} & set(changed):
            self._send_meta(key, settle=True)

    def _send_meta(self, key: str, settle: bool = False) -> None:
        loop = asyncio.get_event_loop()
        old = self._meta_due.pop(key, None)
        if old:
            old.cancel()

        def send():
            self._meta_due.pop(key, None)
            r = self._receiver(key)
            st = self.streams.get(key)
            if r is None or st is None or key not in self.sessions or not st.running:
                return
            m = st.meta
            self.registry.command(r, "meta", self._label(key), _clean(m.get("title", "")),
                                  _clean(m.get("artist", "")), _clean(m.get("album", "")))
        if settle:
            self._meta_due[key] = loop.call_later(META_SETTLE, send)
        else:
            send()

    def _meta(self, key: str, kind: str, **meta) -> None:
        self._known.setdefault((key, kind), {}).update(meta)
        if self._session_kind(key) == kind:
            self.stream(key).set_meta(**meta)

    def set_volume(self, key: str, vol: int | None) -> None:
        r = self._receiver(key)
        if vol is None or r is None or key not in self.sessions:
            return
        self.stream(key).set_meta(volume=vol)
        self.registry.command(r, "vol", vol)

    def set_paused(self, key: str, paused: bool) -> None:
        sess = self.sessions.get(key)
        if sess is None:
            return
        sess.paused_at = time.monotonic() if paused else None
        self.stream(key).set_meta(paused=paused)

    # -- what the processes say ------------------------------------------------
    def hook_message(self, msg: dict) -> None:
        kind = msg.get("kind")
        if kind == "withdraw":
            asyncio.ensure_future(self._withdraw((str(msg.get("instance", "")), str(msg.get("name")))))
            return
        inst = self._instance(str(msg.get("instance", "")))
        if inst is None:
            return
        if kind == "publish":
            asyncio.ensure_future(self._publish(inst, msg))
        elif kind == "event" and isinstance(inst, SpotifyInstance):
            self.spotify_event(inst, msg.get("env") or {})

    def airplay_item(self, inst: Instance, kind: str, code: str, data: bytes) -> None:
        key = inst.key
        text = data.decode("utf-8", "replace")
        if kind == "ssnc" and code == "pbeg":
            self.begin(key, "airplay")
            return
        if kind == "core" and code in ("minm", "asar", "asal"):
            self._meta(key, "airplay", **{{"minm": "title", "asar": "artist", "asal": "album"}[code]: text})
            return
        if kind == "ssnc" and code == "snam":
            self._meta(key, "airplay", sender=text)
            return
        if kind == "ssnc" and code == "PICT":
            version = self._known.get((key, "airplay"), {}).get("art_version", 0) + 1
            self._meta(key, "airplay", art=data or None, art_type=_image_type(data) if data else None,
                       art_version=version)
            return
        if self._session_kind(key) != "airplay":
            return
        if kind == "ssnc" and code == "pvol":
            self.set_volume(key, airplay_volume(text))
        elif kind == "ssnc" and code == "pfls":
            self.set_paused(key, True)
        elif kind == "ssnc" and code == "prsm":
            self.set_paused(key, False)
        elif kind == "ssnc" and code == "pend":
            self.end(key, kick=False)

    def spotify_event(self, inst: Instance, env: dict) -> None:
        key = inst.key
        ev = env.get("PLAYER_EVENT", "")
        if ev == "playing":
            self.begin(key, "spotify")
            self.set_paused(key, False)
            return
        if ev == "session_client_changed" and env.get("CLIENT_NAME"):
            self._meta(key, "spotify", sender=env["CLIENT_NAME"])
            return
        if ev == "track_changed":
            artists = ", ".join(a for a in (env.get("ARTISTS") or env.get("SHOW_NAME") or "").split("\n") if a)
            covers = [c for c in (env.get("COVERS") or "").split("\n") if c]
            self._meta(key, "spotify", title=env.get("NAME", ""), artist=artists, album=env.get("ALBUM", ""),
                       art_url=covers[0] if covers else None)
            return
        if self._session_kind(key) != "spotify":
            return
        if ev == "paused":
            self.set_paused(key, True)
        elif ev == "volume_changed":
            try:
                self.set_volume(key, round(int(env.get("VOLUME", "")) * 100 / 65535))
            except ValueError:
                pass
        elif ev in ("stopped", "session_disconnected"):
            self.end(key, kick=False)

    # -- advertising AirPlay --------------------------------------------------
    async def _publish(self, inst: Instance, msg: dict) -> None:
        from .mdns import service_info

        if self.shared is None:
            return
        key = (inst.id, str(msg.get("name")))
        if key in self.published:
            return
        try:
            info = service_info(str(msg["type"]), str(msg["name"]), int(msg["port"]),
                                [str(t) for t in msg.get("txt") or []], inst.ip)
            zc = await asyncio.to_thread(self.shared.get)
            await asyncio.to_thread(zc.register_service, info, None, True)
        except Exception as exc:
            log.error("%s: could not advertise the AirPlay speaker: %s", inst.name, exc)
            return
        self.published[key] = info
        log.info("%s: advertising AirPlay speaker %r on %s:%s", inst.name, info.name, inst.ip, info.port)

    async def _withdraw(self, key: tuple[str, str]) -> None:
        info = self.published.pop(key, None)
        if info is None or self.shared is None or not self.shared.started:
            return
        try:
            zc = self.shared.get()
            await asyncio.to_thread(zc.unregister_service, info)
        except Exception as exc:
            log.debug("withdrawing %s: %s", key, exc)

    # -- HTTP --------------------------------------------------------------------
    def routes(self) -> list:
        return [web.get("/live/{sid}", self.serve_live)]

    async def serve_live(self, req: web.Request) -> web.StreamResponse:
        key = self._ids.get(req.match_info["sid"])
        st = self.streams.get(key) if key else None
        if st is None or key not in self.sessions:
            raise web.HTTPNotFound(text="nothing is streaming")
        return await st.serve(req)

    def art(self, r: Receiver) -> tuple[bytes, str] | None:
        st = self.streams.get(r.key)
        if st is None or not st.meta.get("art"):
            return None
        return st.meta["art"], st.meta.get("art_type") or "image/jpeg"


class _HookProtocol(asyncio.DatagramProtocol):
    def __init__(self, mgr: Speakers):
        self.mgr = mgr

    def datagram_received(self, data, addr) -> None:
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if isinstance(msg, dict):
            try:
                self.mgr.hook_message(msg)
            except Exception:
                log.exception("speaker event")


def _clean(s: str) -> str:
    return " ".join(str(s or "").split())[:95]


def _sh(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
