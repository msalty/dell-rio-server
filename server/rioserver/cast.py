"""Google Cast: Chromecast, Chromecast Audio, Google Home / Nest speakers,
speaker groups and speakers or TVs with Chromecast built-in.

Devices announce themselves over mDNS (_googlecast._tcp). For each one the
server keeps a connection open (pychromecast) and plays tracks through
Google's Default Media Receiver: the device is handed the track's
/api/stream URL with its title, artist and cover, fetches the MP3 from this
server and reports its progress back. While a track plays, the next one is
added to the device's own queue with a preload time, so it goes straight on
without a gap; QueuePlayer follows along (and starts the next track itself
if the device doesn't).

A cast device can also be an AirPlay or Spotify Connect speaker
(speakers.py): the session is handed to it as a LIVE stream of /live/<id>,
which it fetches from this server like any track.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from . import players

log = logging.getLogger(__name__)

APP_MEDIA_RECEIVER = "CC1AD845"  # Google's Default Media Receiver
APP_BACKDROP = "E8C28D3C"  # the idle screen / ambient mode, i.e. nothing running
LAUNCH_GRACE = 15.0  # seconds during which another app's status is from before our launch
STATES = {"PLAYING": "play", "BUFFERING": "buffer", "PAUSED": "pause", "IDLE": "stop"}
PRELOAD_SECONDS = 20  # how long before the end of a track the device starts fetching the next
_STREAM_RE = re.compile(r"/api/stream/(\d+)(?:\?.*)?$")


def _track_id(content_id: str | None, base: str) -> int | None:
    if not content_id or not content_id.startswith(base):
        return None
    m = _STREAM_RE.search(content_id)
    return int(m.group(1)) if m else None


def track_metadata(t: dict, base: str) -> dict:
    """MusicTrackMediaMetadata for a library track."""
    meta = {
        "metadataType": 3,
        "title": t["title"],
        "artist": t["artist"],
        "albumName": t["album"],
        "albumArtist": t["album_artist"],
        "images": [{"url": f"{base}/api/art/album/{t['album_id']}"}],
    }
    if t.get("track_no"):
        meta["trackNumber"] = t["track_no"]
    if t.get("disc_no"):
        meta["discNumber"] = t["disc_no"]
    year = t.get("year") or t.get("album_year")
    if year:
        meta["releaseDate"] = str(year)
    return meta


class CastPlayer(players.QueuePlayer):
    kind = "cast"
    can_preload = True

    def __init__(self, cast_info, cfg, zconf=None):
        model = cast_info.model_name or "Chromecast"
        super().__init__(f"cast-{cast_info.uuid}", cast_info.friendly_name or "Chromecast", cast_info.host,
                         "Speaker group" if model == "Google Cast Group" else model)
        self.cast_info = cast_info
        self.cfg = cfg
        self.zconf = zconf
        self.cc = None
        self._dmr = None
        self._ended_session = None
        self._launched_at = float("-inf")
        self._base = ""
        self._live_url = ""  # the live stream it was handed (AirPlay, Spotify)
        # Told whether each media status is of that stream (speakers.py).
        self.live_report = None

    @property
    def base(self) -> str:
        """Where this device fetches music from (this server, as it sees it)."""
        if not self._base:
            self._base = players.server_url(self.cfg, self.ip)
        return self._base

    def connect(self) -> None:
        self._submit(self._connect)

    def update(self, cast_info) -> None:
        """mDNS has new details (a renamed device, a new address)."""
        self.cast_info = cast_info
        self.ip = cast_info.host
        self._base = ""
        if cast_info.friendly_name:
            self.default_name = cast_info.friendly_name

    def close(self) -> None:
        cc = self.cc
        if cc is not None:
            try:
                cc.disconnect(timeout=0)
            except Exception:
                pass
        super().close()

    def _connect(self) -> None:
        import pychromecast
        from pychromecast.controllers.media import DefaultMediaReceiverController

        cc = pychromecast.get_chromecast_from_cast_info(self.cast_info, self.zconf, timeout=10, retry_wait=5)
        self._dmr = DefaultMediaReceiverController()
        cc.register_handler(self._dmr)
        cc.register_status_listener(self)
        cc.media_controller.register_status_listener(self)
        cc.register_connection_listener(self)
        self.cc = cc
        cc.start()

    # -- device calls (worker thread) ------------------------------------
    def _wait_connected(self) -> None:
        if self.cc is None:
            raise ConnectionError("not connected yet")
        self.cc.wait(timeout=10)

    def _media(self, track: dict) -> dict:
        base = self.base
        return {"contentId": f"{base}{self.stream_path(track)}", "streamType": "BUFFERED",
                "contentType": "audio/mpeg", "metadata": track_metadata(track, base)}

    # -- live streams (AirPlay, Spotify; see speakers.py) ---------------------
    def play_live(self, path: str, info: dict) -> None:
        with self._lock:
            self._preloaded = self._preload_for = None
            self._set_state("buffer", 0)
            self._live_url = f"{self.base}{path}"
        self._submit(self._load_live, self._live_url, info)

    def live_meta(self, info: dict) -> None:
        """The Default Media Receiver can't change what it shows without
        loading the stream again (a gap in the sound), so a cast device keeps
        the details the session started with."""

    def _live_metadata(self, info: dict) -> dict:
        art = info.get("art") or ""
        if art.startswith("/"):
            art = f"{self.base}{art}"
        meta = {"metadataType": 3, "title": info.get("title") or info.get("label") or "Live",
                "artist": info.get("artist") or info.get("label") or "", "albumName": info.get("album") or ""}
        if art:
            meta["images"] = [{"url": art}]
        return meta

    def _load_live(self, url: str, info: dict) -> None:
        if self._live_url != url:
            return  # a newer load or the end came first
        self._wait_connected()
        self._launched_at = time.monotonic()
        self._dmr.play_media(url, "audio/mpeg", stream_type="LIVE", metadata=self._live_metadata(info))

    def _report_live(self, live: bool) -> None:
        if self.live_report is not None:
            self.live_report(live)

    def _device_load(self, track: dict, start_seconds: float = 0.0) -> None:
        self._live_url = ""
        self._wait_connected()
        base = self.base
        self._launched_at = time.monotonic()
        self._dmr.play_media(
            f"{base}{self.stream_path(track)}", "audio/mpeg",
            stream_type="BUFFERED", metadata=track_metadata(track, base),
            current_time=start_seconds or None)

    def _device_preload(self, track: dict) -> None:
        """Add the next track to the device's queue, to start when this one ends."""
        self._wait_connected()
        status = self.cc.media_controller.status
        if not status.media_session_id:
            raise ConnectionError("no media session yet")
        self._dmr.send_message({
            "type": "QUEUE_INSERT",
            "mediaSessionId": status.media_session_id,
            "items": [{"media": self._media(track), "autoplay": True, "startTime": 0,
                       "preloadTime": PRELOAD_SECONDS}],
        }, inc_session_id=True)

    def _device_pause(self) -> None:
        self._wait_connected()
        self.cc.media_controller.pause()

    def _device_resume(self) -> None:
        self._wait_connected()
        self.cc.media_controller.play()

    def _device_stop(self) -> None:
        self._live_url = ""
        self._wait_connected()
        if self.cc.app_id == APP_MEDIA_RECEIVER:
            self.cc.media_controller.stop()

    def _device_seek(self, seconds: float) -> None:
        self._wait_connected()
        self.cc.media_controller.seek(seconds)

    def _device_volume(self, level: float) -> None:
        self._wait_connected()
        self.cc.set_volume(level)

    # -- pychromecast listeners (its socket thread) ----------------------
    def new_cast_status(self, status) -> None:
        self._on_volume(round(status.volume_level * 100))
        app = status.app_id
        if app in (None, APP_BACKDROP):
            self._on_other_app("")
            if self._live_url and time.monotonic() - self._launched_at > LAUNCH_GRACE:
                self._report_live(False)  # the device stopped the stream (its own buttons, say)
        elif app != APP_MEDIA_RECEIVER and time.monotonic() - self._launched_at > LAUNCH_GRACE:
            self._on_other_app(status.display_name or "another app")
            if self._live_url:
                self._report_live(False)

    def new_media_status(self, status) -> None:
        base = self.base
        live = self._live_url
        if live and status.content_id == live:
            st = STATES.get(status.player_state)
            if st in ("play", "buffer", "pause"):
                self._set_state(st, (status.adjusted_current_time or 0) * 1000)
                self.last_seen = time.time()
                self._report_live(True)
            elif st == "stop" and time.monotonic() - self._launched_at > 2:
                self._set_state("stop", 0)
                self._report_live(False)  # it stopped, or gave up on the stream
            return
        tid = _track_id(status.content_id, base)
        if tid is None:
            if status.content_id and status.player_state != "IDLE" \
                    and time.monotonic() - self._launched_at > LAUNCH_GRACE:
                # Something else was cast through the Default Media Receiver.
                self._on_other_app(status.title or "another app")
                if live:
                    self._report_live(False)
            return
        st = STATES.get(status.player_state)
        if st is None:
            return
        pos = (status.adjusted_current_time or 0) * 1000
        if st == "stop":
            self._on_status("stop", 0, tid)
            reason = status.idle_reason
            # One media session can play several tracks (the device's queue).
            ended = (status.media_session_id, tid)
            if reason in ("FINISHED", "ERROR") and ended != self._ended_session:
                self._ended_session = ended
                self._on_ended(tid, failed=reason == "ERROR")
        else:
            self._on_status(st, pos, tid)

    def load_media_failed(self, queue_item_id: int, error_code: int) -> None:
        log.warning("%s: could not play the track (error %s)", self.name, error_code)
        t = self.current
        if t:
            self._on_ended(t["id"], failed=True)

    def new_connection_status(self, status) -> None:
        connected = status.status == "CONNECTED"
        if connected != self.online:
            log.info("%s (%s) %s", self.name, self.model, "connected" if connected else "disconnected")
        self._on_online(connected)


class CastDiscovery:
    """Finds cast devices on the network and adds them to the registry."""

    def __init__(self, cfg, registry, loop, shared=None):
        from .mdns import SharedZeroconf

        self.cfg = cfg
        self.registry = registry
        self.loop = loop
        self.shared = shared or SharedZeroconf()
        self.browser = None
        self.zconf = None

    def start(self) -> None:
        from pychromecast.discovery import CastBrowser, SimpleCastListener

        self.zconf = self.shared.get()
        listener = SimpleCastListener(self._found, self._lost, self._found)
        self.browser = CastBrowser(listener, self.zconf)
        self.browser.start_discovery()

    def close(self) -> None:
        if self.browser is not None:
            # Also closes zeroconf, which won't do its blocking goodbyes on
            # the event loop's thread.
            t = threading.Thread(target=self.browser.stop_discovery, daemon=True)
            t.start()
            t.join(5)

    # zeroconf's thread -> the event loop, which owns the registry
    def _found(self, uuid, service) -> None:
        info = self.browser.devices.get(uuid)
        if info is not None:
            self.loop.call_soon_threadsafe(self._add, info)

    def _lost(self, uuid, service, cast_info) -> None:
        # The device may only have missed an mDNS refresh; pychromecast keeps
        # trying to reconnect and the connection status decides "online".
        log.debug("cast device %s left mDNS", cast_info.friendly_name)

    def _add(self, info) -> None:
        pid = f"cast-{info.uuid}"
        player = self.registry.get(pid)
        if player is not None:
            player.update(info)
            return
        player = CastPlayer(info, self.cfg, self.zconf)
        log.info("found %s %r at %s", player.model, player.name, info.host)
        self.registry.add(player)
        player.connect()
