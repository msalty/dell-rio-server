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
"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import ids
from .ssdp import local_ip_for

log = logging.getLogger(__name__)

RESTART_MS = 3000  # "previous" restarts the track if it has played this long
MAX_ERRORS = 3  # tracks in a row that fail to play before the queue stops
VOLUME_HOLD = 1.5  # seconds to trust a volume we set over the device's reports


def server_url(cfg, peer_ip: str) -> str:
    """The base URL a device at `peer_ip` can fetch music from."""
    return f"http://{cfg.server_ip or local_ip_for(peer_ip)}:{cfg.http_port}"


def play_order(count: int, index: int, shuffle: bool) -> tuple[list[int], int]:
    """Positions in the order to play them, and where to start. Shuffling
    puts the chosen track first, as the receiver firmware does."""
    order = list(range(count))
    if not shuffle:
        return order, max(0, min(index, count - 1))
    head = [order.pop(index)] if 0 <= index < count else []
    random.shuffle(order)
    return head + order, 0


class QueuePlayer:
    kind = ""

    def __init__(self, player_id: str, default_name: str, ip: str, model: str = ""):
        self.id = player_id
        self.ip = ip
        self.default_name = default_name
        self.model = model
        self.label = ""
        self.online = False
        self.first_seen = self.last_seen = time.time()
        self.sleep_at: float | None = None
        self.queue: list[dict] = []
        self.index = 0
        self.shuffle = False
        # What the device last reported.
        self._st = "stop"
        self._pos_ms = 0
        self._pos_at = time.monotonic()
        self._vol: int | None = None
        self._vol_hold = 0.0  # until then, device volume reports predate our change
        self._other_app = ""  # set while another app (Spotify, YouTube, ...) has the device
        self._errors = 0
        self._ended_index: int | None = None  # the queue position whose end was handled
        self._pending: dict[str, float] = {}  # latest volume/seek not yet sent
        self._lock = threading.RLock()
        self._worker = ThreadPoolExecutor(1, thread_name_prefix=f"player-{player_id}")

    @property
    def key(self) -> str:
        return self.id

    @property
    def name(self) -> str:
        return self.label or self.default_name

    @property
    def current(self) -> dict | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

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
            t = self.current if not self._other_app else None
            st = {"st": self._st, "pos": str(self.position_ms()), "vol": "" if self._vol is None else str(self._vol),
                  "qi": str(self.index), "qn": str(len(self.queue)), "shuf": "1" if self.shuffle else "0"}
            if self._other_app:
                st["app"] = self._other_app
            if t:
                st["id"] = f"{ids.track_fid(t['id']):x}"
                st["track"] = {"id": t["id"], "title": t["title"], "artist": t["artist"], "album": t["album"],
                               "album_id": t["album_id"], "duration_ms": t["duration_ms"]}
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
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "state": self.state,
            "sleep_at": self.sleep_at,
        }

    # -- commands from the web interface ---------------------------------
    def play(self, tracks: list[dict], index: int = 0, shuffle: bool = False) -> None:
        if not tracks:
            return
        order, start = play_order(len(tracks), index, shuffle)
        with self._lock:
            self.queue = [tracks[i] for i in order]
            self.shuffle = shuffle
            self._errors = 0
            self._other_app = ""
        self._submit(self._start, start)

    def command(self, verb: str, value=None) -> None:
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
        # "msg" shows text on a receiver's display; speakers have nowhere to put it.

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

    def _start(self, index: int) -> None:
        with self._lock:
            if not 0 <= index < len(self.queue):
                return
            self.index = index
            self._ended_index = None
            track = self.queue[index]
            self._set_state("buffer", 0)
        self._device_load(track)

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

    def _next(self) -> None:
        if self.index + 1 < len(self.queue):
            self._start(self.index + 1)

    def _prev(self) -> None:
        if not self.queue:
            return
        if self.position_ms() > RESTART_MS or self.index == 0:
            self._start(self.index)
        else:
            self._start(self.index - 1)

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
            if self.index + 1 >= len(self.queue):
                self._set_state("stop", 0)
                return
        self._start(self.index + 1)

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
            if track_id is None or not t or t["id"] != track_id:
                return  # an old report, or someone else's media (see _on_other_app)
            self._other_app = ""
            self._set_state(st, pos_ms)
            if st == "play":
                self._errors = 0

    def _on_ended(self, track_id: int, failed: bool) -> None:
        """track_id finished (or could not be played). A device may say so
        more than once; the queue moves on once."""
        with self._lock:
            t = self.current
            if not t or t["id"] != track_id or self._other_app or self._ended_index == self.index:
                return
            self._ended_index = self.index
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
            self.queue = []
            self.index = 0
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
    def _device_load(self, track: dict) -> None:
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
