"""Alarm clock: at a set time, start music on a receiver and fade it in.

Alarms are kept in <data>/alarms.json. Each has a time of day in the time
zone of the browser that set it (so the container's own time zone doesn't
matter), the days it repeats on (none: once), the receiver, what to play
and how long to take to fade up to its volume.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

CHECK_SECONDS = 15
LATE_GRACE = 120  # seconds: an alarm missed by up to this much (a restart) still goes off
SOURCES = ("playlist", "album", "artist", "podcast", "shuffle", "top25", "played")
FADE_STEPS = 24


def _zone(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name) if name else dt.timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return dt.timezone.utc


def next_fire(alarm: dict, after: float) -> float | None:
    """When the alarm next goes off after `after` (a Unix time)."""
    if not alarm.get("enabled"):
        return None
    try:
        hh, mm = (int(x) for x in alarm["time"].split(":"))
    except (KeyError, ValueError):
        return None
    tz = _zone(alarm.get("tz", ""))
    start = dt.datetime.fromtimestamp(after, tz)
    days = alarm.get("days") or []
    for add in range(0, 8):
        day = (start + dt.timedelta(days=add)).date()
        when = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        if when.timestamp() <= after:
            continue
        if days and day.weekday() not in days:
            continue
        return when.timestamp()
    return None


class AlarmClock:
    def __init__(self, path: Path, fire):
        """`fire(alarm)` is a coroutine that starts it (see WebAPI)."""
        self.path = path
        self.fire = fire
        self.alarms: list[dict] = []
        self._task: asyncio.Task | None = None
        self._last_check = time.time()
        try:
            self.alarms = [a for a in json.loads(path.read_text()) if isinstance(a, dict)]
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.alarms, indent=1))
        except OSError as exc:
            log.warning("could not save alarms: %s", exc)

    def listing(self) -> list[dict]:
        now = time.time()
        return [{**a, "next": next_fire(a, now)} for a in self.alarms]

    def get(self, alarm_id: str) -> dict | None:
        return next((a for a in self.alarms if a["id"] == alarm_id), None)

    @staticmethod
    def clean(body: dict, old: dict | None = None) -> dict:
        a = dict(old or {})
        if "time" in body or not old:
            hh, _, mm = str(body.get("time", "07:00")).partition(":")
            if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError("time must be HH:MM")
            a["time"] = f"{int(hh):02d}:{int(mm):02d}"
        if "days" in body or not old:
            a["days"] = sorted({int(d) for d in body.get("days") or [] if 0 <= int(d) <= 6})
        if "receiver" in body or not old:
            a["receiver"] = str(body.get("receiver") or "")
            if not a["receiver"]:
                raise ValueError("choose a receiver")
        if "source" in body or not old:
            src = body.get("source") or {}
            kind = str(src.get("type", "shuffle"))
            if kind not in SOURCES:
                raise ValueError("unknown thing to play")
            a["source"] = {"type": kind, "id": int(src.get("id") or 0), "name": str(src.get("name") or "")[:120]}
        for key, default, lo, hi in (("volume", 40, 1, 100), ("fade", 5, 0, 60)):
            if key in body or not old:
                a[key] = max(lo, min(hi, int(body.get(key, default))))
        for key, default in (("shuffle", False), ("enabled", True)):
            if key in body or not old:
                a[key] = bool(body.get(key, default))
        if "label" in body or not old:
            a["label"] = str(body.get("label") or "")[:60]
        if "tz" in body or not old:
            a["tz"] = str(body.get("tz") or "")[:64]
        a.setdefault("id", uuid.uuid4().hex[:12])
        return a

    def add(self, body: dict) -> dict:
        a = self.clean(body)
        self.alarms.append(a)
        self._save()
        return a

    def update(self, alarm_id: str, body: dict) -> dict:
        old = self.get(alarm_id)
        if old is None:
            raise KeyError(alarm_id)
        a = self.clean(body, old)
        self.alarms[self.alarms.index(old)] = a
        self._save()
        return a

    def delete(self, alarm_id: str) -> None:
        self.alarms = [a for a in self.alarms if a["id"] != alarm_id]
        self._save()

    # -- running -----------------------------------------------------------
    async def check(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        since = max(self._last_check, now - LATE_GRACE)
        self._last_check = now
        for a in list(self.alarms):
            when = next_fire(a, since - 0.001)
            if when is None or when > now:
                continue
            log.info("alarm %s %s: starting %s on %s", a["time"], a.get("label") or "",
                     a["source"]["type"], a["receiver"])
            if not a["days"]:
                a["enabled"] = False  # a one-off alarm is done
                self._save()
            try:
                await self.fire(a)
            except Exception as exc:
                log.warning("alarm %s could not start: %s", a["time"], exc)

    def start(self) -> None:
        async def loop():
            while True:
                await asyncio.sleep(CHECK_SECONDS)
                try:
                    await self.check()
                except Exception:
                    log.exception("alarm check failed")
        self._last_check = time.time()
        self._task = asyncio.get_running_loop().create_task(loop())

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
