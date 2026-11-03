"""Browsers as receivers: a phone, tablet or computer playing through a stereo.

A browser that switches on "Use this device as a receiver" opens a WebSocket
to /api/player/ws and from then on is a player like a Chromecast: the server
keeps its queue (players.QueuePlayer) and tells it what to play, and it
reports back what it is doing.

Server -> browser (JSON):
    {"type": "welcome", "id": "<player id>"}
    {"type": "load", "url": ..., "track": {...}, "start": <seconds>}
    {"type": "preload", "url": ..., "track": {...}}   the next track, to go on to without a gap
    {"type": "pause" | "resume" | "stop"}
    {"type": "seek", "to": <seconds>}
    {"type": "volume", "level": <0..1>}
Browser -> server:
    {"type": "hello", "name": "Living Room", "volume": true|false}
    {"type": "status", "st": "play"|"pause"|"buffer"|"stop", "pos": <ms>, "track_id": <id>, "vol": <0..100>}
    {"type": "ended", "track_id": <id>, "failed": false}

"volume" in the hello says whether the page can set its volume at all:
Safari on iPhone and iPad ignores it (only the device's buttons change it).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from . import players

log = logging.getLogger(__name__)

SEND_TIMEOUT = 5.0


def device_model(user_agent: str) -> str:
    """A short name for the kind of device, from its browser's User-Agent."""
    ua = user_agent or ""
    if "Silk" in ua or re.search(r"\bKF[A-Z]{2,}\b", ua):
        return "Fire tablet"
    for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
                      ("Windows", "Windows PC"), ("CrOS", "Chromebook"), ("Linux", "Linux PC")):
        if key in ua:
            return name
    return "Web browser"


def track_message(track: dict) -> dict:
    return {"id": track["id"], "title": track["title"], "artist": track["artist"], "album": track["album"],
            "album_id": track["album_id"], "duration_ms": track["duration_ms"],
            "podcast_id": track.get("podcast_id")}


class BrowserPlayer(players.QueuePlayer):
    kind = "browser"
    can_preload = True

    def __init__(self, player_id: str, loop: asyncio.AbstractEventLoop, ip: str = "", model: str = "Web browser"):
        super().__init__(player_id, model, ip, model)
        self.loop = loop
        self.ws = None
        self.volume_supported = True
        # The page lost what it was playing (it was reloaded, or the phone
        # dropped it): resuming loads the track again where it was.
        self._device_empty = False
        self._was_playing = False

    def to_json(self) -> dict:
        d = super().to_json()
        d["volume"] = self.volume_supported
        return d

    # -- the connection (event loop) ---------------------------------------
    def attach(self, ws, ip: str, model: str) -> None:
        old = self.ws
        self.ws = ws
        self.ip = ip or self.ip
        self.model = model
        self.default_name = model
        self._on_online(True)
        if old is not None and old is not ws and not old.closed:
            self.loop.create_task(old.close())  # the same browser connected again

    def detach(self, ws) -> None:
        if self.ws is ws:
            self.ws = None
            self._was_playing = self._st in ("play", "buffer")
            self._on_online(False)

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        track_id = msg.get("track_id")
        try:
            track_id = int(track_id) if track_id is not None else None
        except (TypeError, ValueError):
            track_id = None
        if kind == "status":
            st = msg.get("st")
            if st not in ("play", "pause", "buffer", "stop"):
                return
            if track_id is None:
                self._nothing_loaded()
                return
            try:
                pos = max(0.0, float(msg.get("pos") or 0))
            except (TypeError, ValueError):
                pos = 0.0
            if "vol" in msg and self.volume_supported:
                try:
                    self._on_volume(int(msg["vol"]))
                except (TypeError, ValueError):
                    pass
            self._on_status(st, pos, track_id)
        elif kind == "ended" and track_id is not None:
            self._on_ended(track_id, failed=bool(msg.get("failed")))

    def _nothing_loaded(self) -> None:
        """The page has no track: a fresh page after a reload, or it was
        stopped. Keep the queue and the position; carry on if it was playing
        when the connection dropped."""
        with self._lock:
            if self.current is None or self._other_app or self._device_empty:
                return
            self._device_empty = True
            pos = self.position_ms()
            self._set_state("stop" if self._st != "pause" else "pause", pos)
            again = self._was_playing
            self._was_playing = False
        if again:
            self._submit(self._start, self.index, pos)

    def _resume(self) -> None:
        if self._device_empty and self.current:
            self._start(self.index, self._pos_ms)
        else:
            super()._resume()

    # -- the device (worker thread) -----------------------------------------
    def _send(self, msg: dict) -> None:
        ws = self.ws
        if ws is None or ws.closed:
            raise ConnectionError("the browser isn't connected")
        fut = asyncio.run_coroutine_threadsafe(ws.send_str(json.dumps(msg)), self.loop)
        fut.result(timeout=SEND_TIMEOUT)

    @staticmethod
    def _url(track: dict) -> str:
        return f"/api/stream/{track['id']}"  # the page resolves it against the server it came from

    def _device_load(self, track: dict, start_seconds: float = 0.0) -> None:
        self._device_empty = False
        self._send({"type": "load", "url": self._url(track), "track": track_message(track),
                    "start": round(start_seconds, 3)})

    def _device_preload(self, track: dict) -> None:
        self._send({"type": "preload", "url": self._url(track), "track": track_message(track)})

    def _device_pause(self) -> None:
        self._send({"type": "pause"})

    def _device_resume(self) -> None:
        self._send({"type": "resume"})

    def _device_stop(self) -> None:
        self._send({"type": "stop"})

    def _device_seek(self, seconds: float) -> None:
        self._send({"type": "seek", "to": round(seconds, 3)})

    def _device_volume(self, level: float) -> None:
        if self.volume_supported:
            self._send({"type": "volume", "level": round(level, 3)})
