"""Play history, and where podcast episodes were left off.

Every player reports what it plays: Rio receivers in their status polls,
Chromecasts through their QueuePlayer (sampled here every few seconds) and
the web interface's own player through /api/progress. A track counts as
played once half of it has been heard, or four minutes of it (the rule
Last.fm uses), so skipping through a queue doesn't count everything.

Podcast episodes also have their position saved as they play, whichever
device plays them, so the next device can carry on from there.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

PLAYED_FRACTION = 0.5
PLAYED_MS = 240_000
RESTART_WINDOW = 20.0  # seconds: a device that was just playing an episode is restarting it
SAMPLE_SECONDS = 2.0


@dataclass
class _Now:
    track_id: int
    pos: int = 0
    st: str = ""
    counted: bool = False
    seen: float = 0.0


def played_threshold(duration_ms: int) -> int:
    if not duration_ms:
        return 30_000
    return min(int(duration_ms * PLAYED_FRACTION), PLAYED_MS)


class PlayTracker:
    def __init__(self, library, podcasts=None):
        self.lib = library
        self.podcasts = podcasts
        self._now: dict[str, _Now] = {}
        self._task: asyncio.Task | None = None

    def report(self, device_id: str, device_name: str, track: dict | None, st: str, pos_ms: int) -> None:
        """A device says it is playing (or paused on, ...) `track` at pos_ms."""
        now = time.time()
        prev = self._now.get(device_id)
        if track is None:
            if prev is not None:
                prev.st, prev.seen = st, now
            return
        tid = track["id"]
        if prev is None or prev.track_id != tid:
            if prev is not None:
                self._left(prev)
            prev = self._now[device_id] = _Now(tid)
        elif prev.counted and pos_ms + 10_000 < prev.pos and pos_ms < 5_000:
            prev.counted = False  # started over: another play
        prev.pos, prev.st, prev.seen = int(pos_ms), st, now
        if not prev.counted and st in ("play", "pause", "buffer") and pos_ms >= played_threshold(track.get("duration_ms") or 0):
            prev.counted = True
            try:
                self.lib.record_play(tid, device_name)
            except Exception:
                log.exception("could not record a play")
        if track.get("podcast_id") and self.podcasts is not None and st in ("play", "pause", "buffer", "stop"):
            if st != "stop" or pos_ms:
                self.podcasts.progress(tid, int(pos_ms), track.get("duration_ms") or 0)

    def _left(self, prev: _Now) -> None:
        """A device went on from a track to another one."""
        if self.podcasts is not None:
            self.podcasts.left(prev.track_id, prev.pos)

    def start_position(self, device_id: str, track: dict) -> int:
        """Where a device starting `track` afresh should begin: podcast
        episodes carry on where they were left (on any device), unless this
        device was playing it just now (then it is starting it over)."""
        if not track.get("podcast_id") or self.podcasts is None:
            return 0
        prev = self._now.get(device_id)
        if prev and prev.track_id == track["id"] and prev.st in ("play", "buffer") \
                and time.time() - prev.seen < RESTART_WINDOW:
            return 0
        return self.podcasts.resume_ms(track["id"])

    # -- players the server feeds (Chromecasts) are sampled ---------------
    def start(self, registry) -> None:
        async def loop():
            while True:
                await asyncio.sleep(SAMPLE_SECONDS)
                try:
                    for p in registry.players():
                        st = p.state
                        self.report(p.id, p.name, st.get("track"), st.get("st", ""), int(st.get("pos") or 0))
                except Exception:
                    log.exception("sampling players failed")
        self._task = asyncio.get_running_loop().create_task(loop())

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
