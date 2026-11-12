"""Players that the server feeds one track at a time.

A Rio receiver running the NG firmware keeps its own play queue: the server
sends it "play <node>" and the firmware fetches the track list itself.
Chromecasts (and other network speakers) only play the one URL they are
given, so for them the server keeps the queue and starts the next track when
the device reports that the last one finished.

QueuePlayer is that queue plus the web interface's commands. A subclass
connects it to a kind of device by implementing the _device_* methods and
calling the _on_* methods when the device reports something. Device calls
can block, so they all run on the player's own worker thread, one at a time
and in the order they were asked for.

A device that can hold a second track (_device_preload) is handed the next
one while the current one plays, so it can go straight on without a gap.
It then reports the next track playing, and the queue follows it.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import ids
from .playqueue import PlayQueue, play_order  # noqa: F401  (play_order is part of this module's API)
from .ssdp import local_ip_for

log = logging.getLogger(__name__)

RESTART_MS = 3000  # "previous" restarts the track if it has played this long
MAX_ERRORS = 3  # tracks in a row that fail to play before the queue stops
VOLUME_HOLD = 1.5  # seconds to trust a volume we set over the device's reports
PRELOAD_GRACE = 4.0  # seconds a device may take to go on to the track it was handed in advance
REPEATS = ("off", "all", "one")


def server_url(cfg, peer_ip: str) -> str:
    """The base URL a device at `peer_ip` can fetch music from."""
    return f"http://{cfg.server_ip or local_ip_for(peer_ip)}:{cfg.http_port}"


def track_state(t: dict) -> dict:
    """The track details the web interface shows for what a player plays."""
    return {"id": t["id"], "title": t["title"], "artist": t["artist"], "album": t["album"],
            "album_id": t["album_id"], "duration_ms": t["duration_ms"],
            "podcast_id": t.get("podcast_id")}


class QueuePlayer:
    kind = ""
    can_preload = False  # the device can be handed the next track in advance

    def __init__(self, player_id: str, default_name: str, ip: str, model: str = ""):
        self.id = player_id
        self.ip = ip
        self.default_name = default_name
        self.model = model
        self.label = ""
        self.online = False
        self.first_seen = self.last_seen = time.time()
        self.sleep_at: float | None = None
        self.pq = PlayQueue()
        self.repeat = "off"
        # Where to start a track that is started afresh (podcast resume):
        # (player id, track) -> milliseconds. Set by the registry.
        self.start_position: Callable[[str, dict], int] | None = None
        # What the device last reported.
        self._st = "stop"
        self._pos_ms = 0
        self._pos_at = time.monotonic()
        self._vol: int | None = None
        self._vol_hold = 0.0  # until then, device volume reports predate our change
        self._other_app = ""  # set while another app (Spotify, YouTube, ...) has the device
        self._errors = 0
        self._ended_index: int | None = None  # the queue position whose end was handled
        self._preloaded: dict | None = None  # queue entry handed to the device in advance
        self._preload_for: dict | None = None  # the entry that was playing when it was
        self._pending: dict[str, float] = {}  # latest volume/seek not yet sent
        self._lock = threading.RLock()
        self._worker = ThreadPoolExecutor(1, thread_name_prefix=f"player-{player_id}")
        # AirPlay / Spotify Connect (speakers.py), for players that can play a
        # live stream (browsers): the session playing, the switches, and
        # whether a queue it replaced can be resumed.
        self.live: dict | None = None
        self.speakers: dict | None = None
        self.resumable = False
        # Set by the registry: sees commands first, so they can end a live
        # session; returns True if the command is dealt with.
        self.command_hook: Callable[[str], bool] | None = None
        # Set by the registry: what to add to a track's stream URL so that it
        # comes with this player's equaliser and balance (eq.py), or "".
        self.stream_query: Callable[[], str] | None = None

    @property
    def key(self) -> str:
        return self.id

    @property
    def name(self) -> str:
        return self.label or self.default_name

    # The queue, as the rest of the server (and the tests) read it.
    @property
    def queue(self) -> list[dict]:
        return self.pq.items

    @property
    def index(self) -> int:
        return self.pq.index

    @property
    def shuffle(self) -> bool:
        return self.pq.shuffle

    @property
    def current(self) -> dict | None:
        return self.pq.current

    def stream_path(self, track: dict) -> str:
        """The server path this player fetches a track from."""
        return f"/api/stream/{track['id']}{self.stream_query() if self.stream_query else ''}"

    def reload(self) -> None:
        """Fetch the playing track again from where it is (its sound changed)."""
        with self._lock:
            if self._st not in ("play", "buffer") or not self.current or self.live or self._other_app:
                return
            index, pos = self.index, self.position_ms()
        self._submit(self._start, index, pos)

    def position_ms(self) -> int:
        pos = self._pos_ms
        if self._st == "play":
            pos += int((time.monotonic() - self._pos_at) * 1000)
        t = self.current
        return min(pos, t["duration_ms"]) if t and t["duration_ms"] else pos

    @property
    def state(self) -> dict:
        """Playback state in the shape the NG firmware reports it."""
        with self._lock:
            t = self.current if not self._other_app and not self.live else None
            st = {"st": self._st, "pos": str(self.position_ms()), "vol": "" if self._vol is None else str(self._vol),
                  "qi": str(self.index), "qn": str(len(self.pq)), "shuf": "1" if self.shuffle else "0",
                  "rep": self.repeat}
            if self._other_app:
                st["app"] = self._other_app
            if self.live:
                st["app"] = self.live["app"]
                st["live"] = self.live
                if self.live.get("paused") and st["st"] == "play":
                    st["st"] = "pause"
            if t:
                st["id"] = f"{ids.track_fid(t['id']):x}"
                st["track"] = track_state(t)
            return st

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "ip": self.ip,
            "mac": None,
            "name": self.name,
            "label": self.label,
            "model": self.model,
            "firmware": None,
            "stage": "running",
            "online": self.online,
            "controllable": self.online,
            "queue_editable": True,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "state": self.state,
            "sleep_at": self.sleep_at,
            "speakers": self.speakers,
            "resumable": self.resumable and not self.live,
            "queue_saved": self.resumable,
        }

    # -- commands from the web interface ---------------------------------
    def _hooked(self, verb: str) -> bool:
        return self.command_hook is not None and self.command_hook(verb)

    def play(self, tracks: list[dict], index: int = 0, shuffle: bool = False, start_ms: int | None = None) -> None:
        if not tracks or self._hooked("play"):
            return
        with self._lock:
            start = self.pq.load(tracks, index, shuffle)
            self._errors = 0
            self._other_app = ""
        self._submit(self._start, start, start_ms)

    def command(self, verb: str, value=None) -> None:
        if self._hooked(verb):
            return
        if verb in ("vol", "seek"):
            try:
                number = max(0, int(float(value)))
            except (TypeError, ValueError):
                return
            if verb == "vol":
                number = min(100, number)
                self._vol = number  # show it right away
                self._vol_hold = time.monotonic() + VOLUME_HOLD
            # While a slider is dragged only the latest value matters.
            with self._lock:
                queued = verb in self._pending
                self._pending[verb] = number
            if not queued:
                self._submit(self._send_pending, verb)
        elif verb == "toggle":
            self._submit(self._pause if self._st in ("play", "buffer") else self._resume)
        elif verb in ("pause", "resume", "stop", "next", "prev"):
            self._submit(getattr(self, "_" + verb))
        elif verb == "shuffle":
            self.edit("shuffle", on=str(value) in ("1", "true", "on"))
        elif verb == "repeat":
            if value in REPEATS:
                with self._lock:
                    self.repeat = value
                    self._preload_changed()
        # "msg" shows text on a receiver's display; speakers have nowhere to put it.

    def edit(self, op: str, tracks: list[dict] | None = None, pos: int = 0, to: int = 0, on: bool = False) -> None:
        """Change the queue: "next" and "add" tracks, "move" pos to `to`,
        "remove" pos, "clear" what's still to come, "jump" to pos (play it),
        "shuffle" on or off."""
        if self._hooked("queue"):
            return
        with self._lock:
            was_empty = not self.pq.items
            if op == "next":
                self.pq.insert_next(tracks or [])
            elif op == "add":
                self.pq.append(tracks or [])
            elif op == "move":
                self.pq.move(pos, to)
            elif op == "remove":
                self.pq.remove(pos)
            elif op == "clear":
                self.pq.clear_upcoming()
            elif op == "shuffle":
                self.pq.set_shuffle(on)
            elif op == "jump":
                if 0 <= pos < len(self.pq):
                    self._submit(self._start, pos)
                return
            else:
                raise ValueError(f"unknown queue change {op!r}")
            if was_empty and self.pq.items and op in ("next", "add"):
                self._errors = 0
                self._other_app = ""
                self._submit(self._start, self.pq.index)
                return
            self._preload_changed()

    def close(self) -> None:
        self._worker.shutdown(wait=False, cancel_futures=True)

    # -- queue logic (worker thread) -------------------------------------
    def _submit(self, fn, *args) -> None:
        def run():
            try:
                fn(*args)
            except Exception as exc:  # a device can drop off the network at any moment
                log.warning("%s: %s failed: %s", self.name, getattr(fn, "__name__", fn), exc)
        try:
            self._worker.submit(run)
        except RuntimeError:
            pass  # closed

    def _send_pending(self, verb: str) -> None:
        with self._lock:
            number = self._pending.pop(verb, None)
        if number is None:
            return
        if verb == "vol":
            self._vol_hold = time.monotonic() + VOLUME_HOLD
            self._device_volume(number / 100)
        else:
            self._seek(int(number))

    def _start(self, index: int, start_ms: int | None = None) -> None:
        with self._lock:
            if not 0 <= index < len(self.pq):
                return
            self.pq.index = index
            self._ended_index = None
            self._preloaded = self._preload_for = None
            track = self.pq.items[index]
            if start_ms is None and self.start_position is not None:
                try:
                    start_ms = self.start_position(self.id, track)
                except Exception:
                    log.exception("start position for %s", track.get("title"))
            start_ms = max(0, int(start_ms or 0))
            if track.get("duration_ms") and start_ms >= track["duration_ms"]:
                start_ms = 0
            self._set_state("buffer", start_ms)
        self._device_load(track, start_ms / 1000)

    def _pause(self) -> None:
        self._device_pause()

    def _resume(self) -> None:
        if self._st == "pause" and not self._other_app:
            self._device_resume()
        elif self.current:
            self._start(self.index)

    def _stop(self) -> None:
        self._device_stop()
        self._set_state("stop", 0)

    def _next_index(self) -> int | None:
        if self.index + 1 < len(self.pq):
            return self.index + 1
        if self.repeat == "all" and len(self.pq):
            return 0
        return None

    def _next(self) -> None:
        nxt = self._next_index()
        if nxt is not None:
            self._start(nxt)

    def _prev(self) -> None:
        if not len(self.pq):
            return
        if self.position_ms() > RESTART_MS or self.index == 0:
            self._start(self.index, 0)
        else:
            self._start(self.index - 1, 0)

    def _seek(self, ms: int) -> None:
        t = self.current
        if not t or self._st == "stop":
            return
        if t["duration_ms"]:
            ms = min(ms, t["duration_ms"] - 1)
        self._device_seek(ms / 1000)
        self._set_state(self._st, ms)

    def _advance(self, failed: bool) -> None:
        with self._lock:
            self._errors = self._errors + 1 if failed else 0
            if self._errors >= MAX_ERRORS:
                log.warning("%s: %d tracks in a row failed to play; stopping", self.name, self._errors)
                self._set_state("stop", 0)
                return
            if self.repeat == "one" and not failed:
                nxt = self.index
            else:
                nxt = self._next_index()
            if nxt is None:
                self._set_state("stop", 0)
                return
        self._start(nxt, 0 if nxt == self.index else None)

    # -- handing the device the next track (worker thread) ---------------
    def _preload_target(self) -> dict | None:
        """The entry the device should go on to by itself, if any."""
        if not self.can_preload or self.repeat == "one" or self._other_app:
            return None
        nxt = self._next_index()
        return self.pq.items[nxt] if nxt is not None and nxt != self.index else None

    def _preload_changed(self) -> None:
        """The queue changed. A device that was handed the old next track
        still goes on to it; _on_status then corrects course. Hand it the new
        one if nothing was handed over yet for this track."""
        if self._preloaded is None and self._st == "play":
            self._maybe_preload()

    def _maybe_preload(self) -> None:
        with self._lock:
            cur = self.current
            target = self._preload_target()
            if cur is None or target is None or self._preload_for is cur:
                return
            self._preload_for = cur
            self._preloaded = target
        self._submit(self._do_preload, cur, target)

    def _do_preload(self, cur: dict, target: dict) -> None:
        with self._lock:
            if self.current is not cur or self._preloaded is not target:
                return
        try:
            self._device_preload(target)
        except Exception as exc:
            log.info("%s: could not hand over the next track early: %s", self.name, exc)
            with self._lock:
                if self._preloaded is target:
                    self._preloaded = None

    def _check_went_on(self, index: int) -> None:
        """A track ended while the next was handed over: if the device didn't
        go on to it by itself, start it the usual way."""
        with self._lock:
            if self.index != index or self._ended_index != index:
                return
            self._preloaded = None
        log.info("%s: did not go on to the next track by itself; starting it", self.name)
        self._advance(False)

    # -- reports from the device (any thread) ----------------------------
    def _set_state(self, st: str, pos_ms: int) -> None:
        with self._lock:
            self._st = st
            self._pos_ms = int(pos_ms)
            self._pos_at = time.monotonic()

    def _on_status(self, st: str, pos_ms: float, track_id: int | None) -> None:
        """The device is playing (or paused on, ...) track_id, or something
        that isn't ours if track_id is None."""
        with self._lock:
            self.last_seen = time.time()
            t = self.current
            pre = self._preloaded
            if pre is not None and track_id == pre["id"] and (t is None or t["id"] != track_id):
                # The device went on to the track it was handed in advance.
                self._preloaded = self._preload_for = None
                nxt = self._next_index()
                if nxt is not None and self.pq.items[nxt] is pre:
                    self.pq.index = nxt
                    self._ended_index = None
                    self._errors = 0
                    t = pre
                else:
                    # The queue changed since: play what comes next now.
                    if nxt is not None:
                        self._submit(self._start, nxt)
                    return
            if track_id is None or not t or t["id"] != track_id:
                return  # an old report, or someone else's media (see _on_other_app)
            self._other_app = ""
            self._set_state(st, pos_ms)
            if st == "play":
                self._errors = 0
                self._maybe_preload()

    def _on_ended(self, track_id: int, failed: bool) -> None:
        """track_id finished (or could not be played). A device may say so
        more than once; the queue moves on once."""
        with self._lock:
            t = self.current
            if not t or t["id"] != track_id or self._other_app or self._ended_index == self.index:
                return
            self._ended_index = self.index
            if not failed and self._preloaded is not None:
                # It should carry on by itself; check that it did.
                index = self.index
                timer = threading.Timer(PRELOAD_GRACE, lambda: self._submit(self._check_went_on, index))
                timer.daemon = True
                timer.start()
                return
        self._submit(self._advance, failed)

    def _on_other_app(self, name: str) -> None:
        """Another app took over the device; our queue is over. An empty
        name means that app has gone and the device is free again."""
        with self._lock:
            if not name:
                self._other_app = ""
                return
            if self._other_app != name:
                log.info("%s: now in use by %s", self.name, name)
            self._other_app = name
            self.pq.load([])
            self._preloaded = self._preload_for = None
            self._set_state("stop", 0)

    def _on_volume(self, vol: int) -> None:
        if time.monotonic() < self._vol_hold:
            return  # a report from before the volume we're setting arrived
        self._vol = max(0, min(100, vol))

    def _on_online(self, online: bool) -> None:
        self.online = online
        self.last_seen = time.time()
        if not online and self._st in ("play", "buffer"):
            self._set_state("stop", self.position_ms())  # until it reports again

    # -- the device (worker thread) --------------------------------------
    def _device_load(self, track: dict, start_seconds: float = 0.0) -> None:
        raise NotImplementedError

    def _device_preload(self, track: dict) -> None:
        raise NotImplementedError

    def _device_pause(self) -> None:
        raise NotImplementedError

    def _device_resume(self) -> None:
        raise NotImplementedError

    def _device_stop(self) -> None:
        raise NotImplementedError

    def _device_seek(self, seconds: float) -> None:
        raise NotImplementedError

    def _device_volume(self, level: float) -> None:
        raise NotImplementedError
