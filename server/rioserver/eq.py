"""Equaliser and balance, for players that can't do it themselves.

A Rio receiver, a Chromecast or a phone's browser plays the MP3 it is sent,
so the only place its sound can be shaped is here: the track is decoded,
filtered (ten bands, a preamp and the balance) and encoded again by ffmpeg
while it is fetched. With the equaliser off and the balance centred nothing
of this runs, and the file is sent as it is.

Players seek with byte ranges, so the filtered track has to look like a file
with a size, in which a byte offset is a time:

  * for a browser or a Chromecast (`exact`), it is a constant-bitrate MP3 of
    duration x bitrate bytes, which is also how they work out its length;
  * a Rio receiver was told the size of the original and turns times into
    offsets in proportion (offset = time x size / duration), so for it the
    size stays the original's and the bitrate is the highest that fits in
    it. What is left over at the end is sent as zeros, which no decoder
    plays.

Either way the reply to a range starts encoding at that offset's time and is
exactly as long as was asked for.

A player's settings are {"on", "preamp", "bands", "bal"}: the equaliser's
switch, decibels (-12..12) for the preamp and each band, and the balance
from -1 (left) to 1 (right). The balance applies whether the equaliser is
on or not.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from aiohttp import web

log = logging.getLogger(__name__)

BANDS = (60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000)
WIDTHS = (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.2, 2.5, 4.0, 4.0)  # each band's Q; app.js uses the same
MAX_DB = 12.0
MPEG1_KBPS = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
MPEG2_KBPS = (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)  # sample rates under 32 kHz
SERVER_HEADER = "RioServerNG/1.0"

FLAT = {"on": False, "preamp": 0.0, "bands": [0.0] * len(BANDS), "bal": 0.0}


def _db(value) -> float:
    try:
        return round(max(-MAX_DB, min(MAX_DB, float(value))), 1)
    except (TypeError, ValueError):
        return 0.0


def clean(raw) -> dict:
    """Settings from whatever a client sent: every value present and in range."""
    raw = raw if isinstance(raw, dict) else {}
    bands = raw.get("bands") if isinstance(raw.get("bands"), list) else []
    try:
        bal = round(max(-1.0, min(1.0, float(raw.get("bal", 0)))), 2)
    except (TypeError, ValueError):
        bal = 0.0
    return {"on": bool(raw.get("on")), "preamp": _db(raw.get("preamp")),
            "bands": [_db(bands[i]) if i < len(bands) else 0.0 for i in range(len(BANDS))],
            "bal": bal}


def active(s: dict | None) -> bool:
    """Whether these settings change the sound at all."""
    return bool(s) and (s["bal"] != 0 or (s["on"] and (s["preamp"] != 0 or any(s["bands"]))))


def encode(s: dict) -> str:
    """Settings as a URL parameter: on, preamp and bands in tenths of a dB, balance in hundredths."""
    return ",".join(str(v) for v in [int(s["on"]), round(s["preamp"] * 10), *(round(b * 10) for b in s["bands"]),
                                     round(s["bal"] * 100)])


def decode(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        n = [int(v) for v in text.split(",")]
    except ValueError:
        return None
    if len(n) != len(BANDS) + 3:
        return None
    return clean({"on": n[0], "preamp": n[1] / 10, "bands": [v / 10 for v in n[2:-1]], "bal": n[-1] / 100})


def filters(s: dict, channels: int, sample_rate: int) -> str:
    """The ffmpeg audio filter chain for these settings."""
    parts = []
    boosted = False
    if s["on"]:
        if s["preamp"]:
            parts.append(f"volume={s['preamp']:.1f}dB")
            boosted = boosted or s["preamp"] > 0
        for freq, width, gain in zip(BANDS, WIDTHS, s["bands"]):
            if gain and freq < sample_rate * 0.45:  # a band the track has no room for is left out
                parts.append(f"equalizer=f={freq}:t=q:w={width}:g={gain:.1f}")
                boosted = boosted or gain > 0
    if s["bal"]:
        left, right = 1 - max(0.0, s["bal"]), 1 + min(0.0, s["bal"])
        parts.append(f"pan=stereo|c0={left:.3f}*c0|c1={right:.3f}*{'c0' if channels < 2 else 'c1'}")
    if boosted:
        parts.append("alimiter=limit=0.97:level=disabled")  # turned-up bands must not clip
    return ",".join(parts) or "anull"


def plan(t: dict, exact: bool) -> tuple[int, int] | None:
    """(kbps, size in bytes) of the filtered track as its player sees it; None
    if the track can't be treated this way (no known length)."""
    seconds = (t.get("duration_ms") or 0) / 1000
    audio = max(0, t["size"] - t["data_offset"])
    if seconds <= 0 or audio <= 0:
        return None
    table = MPEG1_KBPS if (t.get("sample_rate") or 44100) >= 32000 else MPEG2_KBPS
    average = audio * 8 / seconds / 1000
    if exact:
        kbps = next((b for b in table if b >= average - 0.5), table[-1])
        return kbps, int(seconds * kbps * 125)
    kbps = max((b for b in table if b * 125 * seconds <= audio * 1.004), default=table[0])
    return kbps, audio


def parse_range(header: str | None, size: int) -> tuple[int, int, bool] | None:
    """(start, stop, partial) for a Range header; None if it starts past the end."""
    start, stop, partial = 0, size - 1, False
    if header and "=" in header:
        spec = header.split("=", 1)[1].split(",")[0].strip()
        a, _, b = spec.partition("-")
        try:
            if a:
                start = int(a)
                stop = int(b) if b else size - 1
            elif b:
                start = max(0, size - int(b))
            partial = True
        except ValueError:
            raise web.HTTPBadRequest(text="bad range")
        if start >= size:
            return None
        stop = min(stop, size - 1)
    return start, stop, partial


async def send(req: web.Request, path: Path, t: dict, s: dict, *, exact: bool, rio_ranges: bool,
               ffmpeg: str = "ffmpeg") -> web.StreamResponse | None:
    """Stream the track with the settings applied, honouring a Range request.
    None if it can't be done, for the caller to send the file as it is."""
    made = plan(t, exact)
    if made is None:
        return None
    kbps, size = made
    rng = parse_range(req.headers.get("Range"), size)
    if rng is None:
        return web.Response(status=416, headers={"Content-Range": f"bytes */{size}", "Server": SERVER_HEADER})
    start, stop, partial = rng
    length = stop - start + 1
    headers = {"Server": SERVER_HEADER, "Content-Type": "audio/mpeg", "Content-Length": str(length),
               "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if partial:
        if rio_ranges:
            headers["Content-Range"] = f"bytes={start}-{stop}/" + (str(size) if stop == size - 1 else "*")
        else:
            headers["Content-Range"] = f"bytes {start}-{stop}/{size}"
    if req.method == "HEAD":
        resp = web.StreamResponse(status=206 if partial else 200, headers=headers)
        await resp.prepare(req)
        return resp

    at = start / size * t["duration_ms"] / 1000
    cmd = [ffmpeg, "-nostdin", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(path), "-map", "0:a:0", "-vn",
           "-af", filters(s, t.get("channels") or 2, t.get("sample_rate") or 44100),
           "-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-ar", str(t.get("sample_rate") or 44100),
           "-write_xing", "0", "-id3v2_version", "0", "-f", "mp3", "pipe:1"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        log.warning("equaliser: can't run %s: %s", ffmpeg, exc)
        return None
    resp = None
    try:
        chunk = await proc.stdout.read(32768)
        if not chunk and at < t["duration_ms"] / 1000 - 1:
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            log.warning("equaliser: ffmpeg gave nothing for %s: %s", path.name, err[-300:])
            return None
        resp = web.StreamResponse(status=206 if partial else 200, headers=headers)
        await resp.prepare(req)
        remaining = length
        while chunk and remaining > 0:
            chunk = chunk[:remaining]
            await resp.write(chunk)
            remaining -= len(chunk)
            if remaining > 0:
                chunk = await proc.stdout.read(32768)
        while remaining > 0:  # the end of the sound comes before the end of the "file"
            pad = bytes(min(32768, remaining))
            await resp.write(pad)
            remaining -= len(pad)
    except (ConnectionResetError, OSError) as exc:
        log.debug("equalised stream to %s ended: %s", req.remote, exc)
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
    return resp


class EqStore:
    """Each player's settings, by its key, kept in a file in the data folder."""

    def __init__(self, path: Path | None = None, ffmpeg: str = "ffmpeg"):
        self._path = path
        self.ffmpeg = ffmpeg
        self._all: dict[str, dict] = {}
        if path:
            try:
                self._all = {str(k): clean(v) for k, v in json.loads(path.read_text()).items()}
            except (OSError, ValueError, AttributeError):
                pass

    @property
    def available(self) -> bool:
        """Whether the server can filter at all (ffmpeg is installed)."""
        return shutil.which(self.ffmpeg) is not None

    def get(self, key: str) -> dict:
        s = self._all.get(key) or FLAT
        return {**s, "bands": list(s["bands"])}

    def set(self, key: str, raw) -> dict:
        s = clean(raw)
        if s == FLAT:
            self._all.pop(key, None)
        else:
            self._all[key] = s
        if self._path:
            try:
                self._path.write_text(json.dumps(self._all, indent=1, sort_keys=True))
            except OSError as exc:
                log.warning("can't save equaliser settings: %s", exc)
        return self.get(key)

    def query(self, key: str) -> str:
        """What to add to a player's stream URL: "?eq=..." or nothing."""
        s = self._all.get(key)
        return f"?eq={encode(s)}" if active(s) and self.available else ""
