"""JSON API behind the web interface, plus album art and browser streaming."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from . import ids
from .firmware import FirmwareStore
from .players import track_state
from .podcasts import PodcastError, make_opml, parse_opml
from .receivers import QueueError, Receiver
from .rio_classic import send_audio
from .rio_ng import NGProtocol, NodeError
from .webplayer import BrowserPlayer, device_model

log = logging.getLogger(__name__)

COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall")
COVER_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
ARTIST_NAMES = ("artist", "artist-photo", "band")
RECEIVER_COMMANDS = {"pause", "resume", "toggle", "stop", "next", "prev", "vol", "seek", "msg", "shuffle", "repeat"}
QUEUE_OPS = {"next", "add", "move", "remove", "clear", "jump", "shuffle"}
SLEEP_FADE = 20  # seconds over which a sleep timer fades the volume out
FADE_STEPS = 24  # volume steps of an alarm's fade-in
MAX_TRACKS = 10000


def _track_json(t: dict) -> dict:
    return {
        "id": t["id"],
        "fid": f"{ids.track_fid(t['id']):x}",
        "title": t["title"],
        "artist": t["artist"],
        "album_artist": t["album_artist"],
        "artist_id": t["artist_id"],
        "album": t["album"],
        "album_id": t["album_id"],
        "genre": t["genre"],
        "year": t["year"] or t["album_year"],
        "track_no": t["track_no"],
        "disc_no": t["disc_no"],
        "duration_ms": t["duration_ms"],
        "bitrate": t["bitrate"],
        "vbr": bool(t["vbr"]),
        "sample_rate": t["sample_rate"],
        "path": t["path"],
        "podcast_id": t.get("podcast_id"),
    }


async def _body(req: web.Request) -> dict:
    try:
        body = await req.json()
    except ValueError:
        raise web.HTTPBadRequest(text="expected JSON")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return body


def _ids(body: dict, key: str = "track_ids") -> list[int]:
    try:
        return [int(x) for x in body.get(key) or []][:MAX_TRACKS]
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=f"{key} must be a list of numbers")


def _int_field(body: dict, key: str, default: int = 0) -> int:
    try:
        return int(body.get(key, default) or 0)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=f"{key} must be a number")


def _images(directory: Path) -> list[Path]:
    try:
        return [f for f in directory.iterdir() if f.suffix.lower() in COVER_EXTS and f.is_file()]
    except OSError:
        return []


def find_cover_file(directory: Path) -> Path | None:
    images = _images(directory)
    for name in COVER_NAMES:
        for f in images:
            if f.stem.lower() == name:
                return f
    return images[0] if len(images) == 1 else None


def find_artist_file(directory: Path) -> Path | None:
    for f in _images(directory):
        if f.stem.lower() in ARTIST_NAMES:
            return f
    return None


def _image_mime(path: Path) -> str:
    return {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(
        path.suffix.lower(), "image/jpeg")


@functools.lru_cache(maxsize=512)
def _load_art(path: str, mtime: float) -> tuple[bytes, str] | None:
    p = Path(path)
    try:
        from mutagen.id3 import ID3
        tags = ID3(path)
        pics = tags.getall("APIC")
        if pics:
            front = next((x for x in pics if x.type == 3), pics[0])
            return front.data, front.mime or "image/jpeg"
    except Exception:
        pass
    cover = find_cover_file(p.parent)
    if cover:
        try:
            return cover.read_bytes(), _image_mime(cover)
        except OSError:
            return None
    return None


class WebAPI:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lib = ctx.library
        self._ng = NGProtocol(ctx)  # resolves what to play for players the server feeds
        self._sleepers: dict[str, asyncio.Task] = {}
        self._faders: dict[str, asyncio.Task] = {}
        self._artist_art: dict[int, Path | int | None] = {}
        self._artist_art_version = None

    def routes(self) -> list:
        return [
            web.get("/api/status", self.status),
            web.post("/api/rescan", self.rescan),
            web.get("/api/artists", self.artists),
            web.get("/api/artists/{id:\\d+}", self.artist),
            web.get("/api/albums", self.albums),
            web.get("/api/albums/{id:\\d+}", self.album),
            web.get("/api/genres", self.genres),
            web.get("/api/genres/{id:\\d+}", self.genre),
            web.get("/api/songs", self.songs),
            web.get("/api/playlists", self.playlists),
            web.get("/api/playlists/{id:\\d+}", self.playlist),
            web.get("/api/recent", self.recent),
            web.get("/api/search", self.search),
            web.get("/api/tracks/{id:\\d+}", self.track),
            web.get("/api/art/album/{id:\\d+}", self.album_art),
            web.get("/api/art/track/{id:\\d+}", self.track_art),
            web.get("/api/art/artist/{id:\\d+}", self.artist_art),
            web.get("/api/stream/{id:\\d+}", self.stream),
            web.get("/api/receivers", self.receivers),
            web.post("/api/receivers/{id}/play", self.receiver_play),
            web.post("/api/receivers/{id}/command", self.receiver_command),
            web.post("/api/receivers/{id}/name", self.receiver_name),
            web.post("/api/receivers/{id}/sleep", self.receiver_sleep),
            web.get("/api/receivers/{id}/queue", self.receiver_queue),
            web.post("/api/receivers/{id}/queue", self.receiver_queue_edit),
            web.post("/api/receivers/{id}/transfer", self.receiver_transfer),
            web.delete("/api/receivers/{id}", self.receiver_forget),
            web.post("/api/receivers/{id}/speakers", self.receiver_speakers),
            web.post("/api/receivers/{id}/resume", self.receiver_resume),
            web.get("/api/receivers/{id}/live/art", self.receiver_live_art),
            web.get("/api/player/ws", self.player_ws),
            web.post("/api/playlists", self.playlist_create),
            web.post("/api/playlists/{id:\\d+}", self.playlist_update),
            web.post("/api/playlists/{id:\\d+}/add", self.playlist_add),
            web.delete("/api/playlists/{id:\\d+}", self.playlist_delete),
            web.get("/api/history/recent", self.history_recent),
            web.get("/api/history/top", self.history_top),
            web.get("/api/history/log", self.history_log),
            web.post("/api/progress", self.progress),
            web.get("/api/tracks/{id:\\d+}/start", self.track_start),
            web.get("/api/podcasts", self.podcasts),
            web.post("/api/podcasts", self.podcast_subscribe),
            web.get("/api/podcasts/opml", self.podcasts_opml),
            web.post("/api/podcasts/opml", self.podcasts_import),
            web.get("/api/podcasts/recent", self.podcasts_recent),
            web.post("/api/podcasts/refresh", self.podcasts_refresh_all),
            web.get("/api/podcasts/{id:\\d+}", self.podcast),
            web.post("/api/podcasts/{id:\\d+}", self.podcast_update),
            web.delete("/api/podcasts/{id:\\d+}", self.podcast_delete),
            web.post("/api/podcasts/{id:\\d+}/refresh", self.podcast_refresh),
            web.post("/api/episodes/{id:\\d+}", self.episode_update),
            web.post("/api/episodes/{id:\\d+}/download", self.episode_download),
            web.delete("/api/episodes/{id:\\d+}/download", self.episode_delete),
            web.get("/api/art/podcast/{id:\\d+}", self.podcast_art),
            web.get("/api/alarms", self.alarms),
            web.post("/api/alarms", self.alarm_add),
            web.post("/api/alarms/{id}", self.alarm_update),
            web.delete("/api/alarms/{id}", self.alarm_delete),
            web.post("/api/alarms/{id}/test", self.alarm_test),
            web.get("/api/firmware", self.firmware),
            web.post("/api/firmware", self.firmware_select),
            web.get("/api/firmware/{name}/download", self.firmware_download),
        ]

    async def _run(self, fn, *args, **kw):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(fn, *args, **kw))

    async def status(self, req: web.Request) -> web.Response:
        lib = self.lib
        stats = await self._run(lib.stats)
        last = lib.last_scan
        fw = self.ctx.firmware
        return web.json_response({
            "version": lib.version,
            "scanning": lib.scanning,
            "music_dir": str(lib.music_dir),
            "stats": stats,
            "last_scan": last.__dict__ if last else None,
            "last_scan_time": lib.last_scan_time,
            "boot_enabled": self.ctx.config.enable_boot,
            "firmware": fw.choice if fw else None,
            "speakers": self.ctx.speakers.available() if self.ctx.speakers is not None else None,
            "receivers": [r.to_json() for r in self.ctx.receivers.all()],
        })

    async def rescan(self, req: web.Request) -> web.Response:
        full = req.query.get("full") == "1"
        self.ctx.watcher.request_scan(full=full)
        return web.json_response({"ok": True, "full": full})

    async def artists(self, req: web.Request) -> web.Response:
        rows = await self._run(self.lib.artists)
        return web.json_response([
            {"id": a["id"], "name": a["name"], "sort": a["sort"], "album_count": a["album_count"],
             "track_count": a["track_count"]} for a in rows])

    async def artist(self, req: web.Request) -> web.Response:
        aid = int(req.match_info["id"])
        a = await self._run(self.lib.artist, aid)
        if not a:
            raise web.HTTPNotFound()
        albums = await self._run(self.lib.albums, artist_id=aid)
        return web.json_response({"id": a["id"], "name": a["name"], "albums": albums})

    async def albums(self, req: web.Request) -> web.Response:
        rows = await self._run(self.lib.albums)
        return web.json_response(rows)

    async def album(self, req: web.Request) -> web.Response:
        alid = int(req.match_info["id"])
        al = await self._run(self.lib.album, alid)
        if not al:
            raise web.HTTPNotFound()
        tracks = await self._run(self.lib.tracks, album_id=alid)
        al["tracks"] = [_track_json(t) for t in tracks]
        al["duration_ms"] = sum(t["duration_ms"] for t in tracks)
        al["genre"] = tracks[0]["genre"] if tracks else None
        return web.json_response(al)

    async def genres(self, req: web.Request) -> web.Response:
        return web.json_response(await self._run(self.lib.genres))

    async def genre(self, req: web.Request) -> web.Response:
        gid = int(req.match_info["id"])
        g = await self._run(self.lib.genre, gid)
        if not g:
            raise web.HTTPNotFound()
        g["albums"] = await self._run(self.lib.albums, genre_id=gid)
        return web.json_response(g)

    async def songs(self, req: web.Request) -> web.Response:
        tracks = await self._run(self.lib.tracks)
        try:
            offset = int(req.query.get("offset", 0))
            limit = int(req.query.get("limit", 500))
        except ValueError:
            raise web.HTTPBadRequest()
        # Where each letter starts, for the A-Z bar (songs are in title order).
        letters: dict[str, int] = {}
        for i, t in enumerate(tracks):
            first = (t["title_sort"] or "#")[:1].upper()
            letters.setdefault(first if "A" <= first <= "Z" else "#", i)
        return web.json_response({"total": len(tracks), "letters": letters,
                                  "tracks": [_track_json(t) for t in tracks[offset:offset + limit]]})

    async def playlists(self, req: web.Request) -> web.Response:
        return web.json_response(await self._run(self.lib.playlists))

    async def playlist(self, req: web.Request) -> web.Response:
        pid = int(req.match_info["id"])
        p = await self._run(self.lib.playlist, pid)
        if not p:
            raise web.HTTPNotFound()
        p["tracks"] = [_track_json(t) for t in await self._run(self.lib.playlist_tracks, pid)]
        return web.json_response(p)

    async def recent(self, req: web.Request) -> web.Response:
        rows = await self._run(self.lib.recently_added, 300)
        seen, albums = set(), []
        for t in rows:
            if t["album_id"] in seen:
                continue
            seen.add(t["album_id"])
            albums.append({"id": t["album_id"], "title": t["album"], "artist": t["album_artist"],
                           "artist_id": t["artist_id"], "year": t["album_year"], "added": t["added"]})
        return web.json_response(albums[:60])

    async def search(self, req: web.Request) -> web.Response:
        q = req.query.get("q", "").strip()
        if not q:
            return web.json_response({"artists": [], "albums": [], "tracks": []})
        res = await self._run(self.lib.search, q)
        res["tracks"] = [_track_json(t) for t in res["tracks"]]
        return web.json_response(res)

    async def track(self, req: web.Request) -> web.Response:
        t = await self._run(self.lib.track, int(req.match_info["id"]))
        if not t:
            raise web.HTTPNotFound()
        return web.json_response(_track_json(t))

    async def _art_response(self, track: dict | None) -> web.Response:
        if not track:
            raise web.HTTPNotFound()
        path = self.lib.track_path(track)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            raise web.HTTPNotFound()
        art = await self._run(_load_art, str(path), mtime)
        if not art:
            raise web.HTTPNotFound()
        data, mime = art
        return web.Response(body=data, content_type=mime,
                            headers={"Cache-Control": "public, max-age=86400"})

    async def album_art(self, req: web.Request) -> web.Response:
        tracks = await self._run(self.lib.tracks, album_id=int(req.match_info["id"]))
        for t in tracks[:3]:
            try:
                return await self._art_response(t)
            except web.HTTPNotFound:
                continue
        raise web.HTTPNotFound()

    def _find_artist_art(self, artist_id: int) -> Path | int | None:
        """An artist.jpg next to the artist's album folders, or else the track
        id whose cover to use (newest album with art first)."""
        albums = self.lib.albums(artist_id=artist_id)
        tracks = [t for t in (self.lib.track(al["first_track"]) for al in reversed(albums)) if t]
        seen = set()
        for t in tracks:
            album_dir = self.lib.track_path(t).parent
            for d in (album_dir.parent, album_dir):
                if d not in seen and d != self.lib.music_dir:
                    seen.add(d)
                    found = find_artist_file(d)
                    if found:
                        return found
        for t in tracks[:6]:
            path = self.lib.track_path(t)
            try:
                if _load_art(str(path), path.stat().st_mtime):
                    return t["id"]
            except OSError:
                continue
        return None

    async def artist_art(self, req: web.Request) -> web.Response:
        aid = int(req.match_info["id"])
        if self._artist_art_version != self.lib.version:
            self._artist_art.clear()
            self._artist_art_version = self.lib.version
        if aid not in self._artist_art:
            self._artist_art[aid] = await self._run(self._find_artist_art, aid)
        found = self._artist_art[aid]
        if found is None:
            raise web.HTTPNotFound()
        if isinstance(found, int):
            return await self._art_response(await self._run(self.lib.track, found))
        try:
            data = await self._run(found.read_bytes)
        except OSError:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type=_image_mime(found),
                            headers={"Cache-Control": "public, max-age=3600"})

    async def track_art(self, req: web.Request) -> web.Response:
        return await self._art_response(await self._run(self.lib.track, int(req.match_info["id"])))

    async def stream(self, req: web.Request) -> web.StreamResponse:
        t = await self._run(self.lib.track, int(req.match_info["id"]))
        if not t:
            raise web.HTTPNotFound()
        path = self.lib.track_path(t)
        if not path.is_file():
            raise web.HTTPNotFound()
        return await send_audio(req, path, 0, t["size"], rio_ranges=False)

    async def receivers(self, req: web.Request) -> web.Response:
        return web.json_response([r.to_json() for r in self.ctx.receivers.all()])

    def _receiver(self, req: web.Request):
        r = self.ctx.receivers.get(req.match_info["id"])
        if r is None:
            raise web.HTTPNotFound(text="unknown receiver")
        if isinstance(r, Receiver) and r.firmware != "ng":
            raise web.HTTPConflict(text="this receiver runs firmware that can't be remote controlled")
        return r

    async def receiver_play(self, req: web.Request) -> web.Response:
        r = self._receiver(req)
        body = await _body(req)
        index = _int_field(body, "index")
        shuffle = 1 if body.get("shuffle") else 0
        start_ms = _int_field(body, "start_ms")
        self._stop_fade(r)
        if "track_ids" in body:
            track_ids = _ids(body)
            if not track_ids:
                raise web.HTTPBadRequest(text="empty queue")
            node = f"q:{self.ctx.receivers.store_queue(track_ids)}"
        elif "album_id" in body:
            node = f"al:{int(body['album_id'])}"
        elif "artist_id" in body:
            node = f"ar:{int(body['artist_id'])}/all"
        elif "playlist_id" in body:
            node = f"pl:{int(body['playlist_id'])}"
        else:
            raise web.HTTPBadRequest(text="nothing to play")
        if isinstance(r, Receiver):
            self.ctx.receivers.command(r, "play", node, index, shuffle)
            if start_ms:
                self.ctx.receivers.command(r, "seek", start_ms)
        else:
            try:
                tracks = await self._run(self._ng.tracks_for, node)
            except NodeError:
                raise web.HTTPBadRequest(text="nothing to play")
            if not tracks:
                raise web.HTTPNotFound(text="nothing to play")
            r.play(tracks, index, bool(shuffle), start_ms or None)
        return web.json_response({"ok": True, "node": node})

    # -- queues ------------------------------------------------------------
    async def receiver_queue(self, req: web.Request) -> web.Response:
        r = self._receiver(req)
        view = self.ctx.receivers.queue_view(r)
        tracks = await self._run(self.lib.tracks_by_ids, view.pop("track_ids"))
        view["tracks"] = [_track_json(t) for t in tracks]
        return web.json_response(view)

    async def receiver_queue_edit(self, req: web.Request) -> web.Response:
        r = self._receiver(req)
        body = await _body(req)
        op = str(body.get("op", ""))
        if op not in QUEUE_OPS:
            raise web.HTTPBadRequest(text="unknown queue change")
        tracks = await self._run(self.lib.tracks_by_ids, _ids(body)) if "track_ids" in body else None
        if op in ("next", "add") and not tracks:
            raise web.HTTPBadRequest(text="nothing to add")
        try:
            self.ctx.receivers.edit(r, op, tracks, _int_field(body, "pos"), _int_field(body, "to"),
                                    bool(body.get("on")))
        except QueueError as exc:
            raise web.HTTPConflict(text=str(exc))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return await self.receiver_queue(req)

    async def receiver_transfer(self, req: web.Request) -> web.Response:
        """Move what one player is playing to another (or, with "to" empty,
        hand it to the browser asking): the queue, the track and where in it."""
        src = self._receiver(req)
        body = await _body(req)
        to = str(body.get("to") or "")
        reg = self.ctx.receivers
        ids, index, pos, playing = reg.now_playing(src)
        if not ids:
            raise web.HTTPConflict(text=f"Nothing is playing on {src.name}")
        tracks = await self._run(self.lib.tracks_by_ids, ids)
        want = ids[index] if 0 <= index < len(ids) else None
        index = next((i for i, t in enumerate(tracks) if t["id"] == want), 0)
        dst = None
        if to:
            dst = reg.get(to)
            if dst is None:
                raise web.HTTPNotFound(text="unknown receiver")
            if dst is src:
                raise web.HTTPBadRequest(text="that is where it's playing already")
            if isinstance(dst, Receiver) and dst.firmware != "ng":
                raise web.HTTPConflict(text="that receiver runs firmware that can't be remote controlled")
            self._stop_fade(dst)
            reg.play(dst, tracks, index, False, pos)
        reg.command(src, "pause")
        return web.json_response({"ok": True, "to": dst.name if dst else "this browser",
                                  "track_ids": [t["id"] for t in tracks], "index": index,
                                  "pos_ms": pos, "playing": playing,
                                  "tracks": [_track_json(t) for t in tracks]})

    async def receiver_command(self, req: web.Request) -> web.Response:
        r = self._receiver(req)
        body = await _body(req)
        cmd = str(body.get("cmd", ""))
        if cmd not in RECEIVER_COMMANDS:
            raise web.HTTPBadRequest(text="unknown command")
        args = [cmd]
        if "value" in body:
            args.append(str(body["value"]).replace("\t", " ")[:200])
        if cmd == "vol":
            self._stop_fade(r)  # turning it down by hand ends an alarm's fade-in
        try:
            self.ctx.receivers.command(r, *args)
        except QueueError as exc:
            raise web.HTTPConflict(text=str(exc))
        return web.json_response({"ok": True})

    async def receiver_name(self, req: web.Request) -> web.Response:
        r = self.ctx.receivers.get(req.match_info["id"])
        if r is None:
            raise web.HTTPNotFound(text="unknown receiver")
        body = await req.json()
        self.ctx.receivers.rename(r, str(body.get("name", "")))
        return web.json_response(r.to_json())

    # -- AirPlay and Spotify Connect ------------------------------------------
    def _speakers(self):
        if self.ctx.speakers is None:
            raise web.HTTPConflict(text="AirPlay and Spotify Connect are turned off on this server")
        return self.ctx.speakers

    async def receiver_speakers(self, req: web.Request) -> web.Response:
        """Switch a receiver's AirPlay / Spotify Connect speakers on or off."""
        r = self._receiver(req)
        if not isinstance(r, Receiver):
            raise web.HTTPBadRequest(text="only Rio receivers can be AirPlay or Spotify speakers")
        body = await _body(req)
        self._speakers().configure(r, **{k: bool(body[k]) for k in ("airplay", "spotify") if k in body})
        return web.json_response(r.to_json())

    async def receiver_resume(self, req: web.Request) -> web.Response:
        """Play the queue an AirPlay or Spotify session replaced, from where it was."""
        r = self._receiver(req)
        saved = self._speakers().resume(r) if isinstance(r, Receiver) else None
        if not saved:
            raise web.HTTPConflict(text="There is nothing to resume")
        tracks = await self._run(self.lib.tracks_by_ids, saved["track_ids"])
        if not tracks:
            raise web.HTTPConflict(text="There is nothing to resume")
        want = saved["track_ids"][saved["index"]] if 0 <= saved["index"] < len(saved["track_ids"]) else None
        index = next((i for i, t in enumerate(tracks) if t["id"] == want), 0)
        self._stop_fade(r)
        self.ctx.receivers.play(r, tracks, index, False, saved["pos_ms"] if want is not None else None)
        return web.json_response({"ok": True})

    async def receiver_live_art(self, req: web.Request) -> web.Response:
        r = self.ctx.receivers.get(req.match_info["id"])
        art = self.ctx.speakers.art(r) if self.ctx.speakers is not None and isinstance(r, Receiver) else None
        if art is None:
            raise web.HTTPNotFound()
        return web.Response(body=art[0], content_type=art[1], headers={"Cache-Control": "max-age=3600"})

    async def receiver_sleep(self, req: web.Request) -> web.Response:
        """Sleep timer: fade out, pause, and put the volume back for next time."""
        r = self._receiver(req)
        body = await req.json()
        try:
            minutes = float(body.get("minutes", 0))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="minutes must be a number")
        old = self._sleepers.pop(r.key, None)
        if old:
            old.cancel()
        r.sleep_at = None
        if minutes > 0:
            delay = min(minutes, 24 * 60) * 60
            r.sleep_at = time.time() + delay
            self._sleepers[r.key] = asyncio.create_task(self._sleep(r, delay))
        return web.json_response(r.to_json())

    async def _sleep(self, r, delay: float) -> None:
        reg = self.ctx.receivers
        try:
            await asyncio.sleep(max(0.0, delay - SLEEP_FADE))
            try:
                start = int(r.state.get("vol") or 0)
            except ValueError:
                start = 0
            steps = 10
            for i in range(1, steps + 1):
                reg.command(r, "vol", round(start * (steps - i) / steps))
                await asyncio.sleep(SLEEP_FADE / steps)
            reg.command(r, "pause")
            if start:
                reg.command(r, "vol", start)
        except asyncio.CancelledError:
            return
        finally:
            if self._sleepers.get(r.key) is asyncio.current_task():
                del self._sleepers[r.key]
                r.sleep_at = None

    async def firmware(self, req: web.Request) -> web.Response:
        fw = self.ctx.firmware
        return web.json_response({
            "enabled": self.ctx.config.enable_boot,
            "selected": fw.choice if fw else None,
            "available": fw.available() if fw else [],
        })

    async def firmware_download(self, req: web.Request) -> web.StreamResponse:
        fw = self.ctx.firmware
        name = req.match_info["name"]
        match = next((a for a in (fw.available() if fw else []) if name in (a["name"], a["file"])), None)
        if match is None:
            raise web.HTTPNotFound()
        path = FirmwareStore.find(fw.firmware_dirs, match["file"])
        return web.FileResponse(path, headers={
            "Content-Disposition": f'attachment; filename="{match["file"]}"',
            "Content-Type": "application/x-tar",
        })

    async def firmware_select(self, req: web.Request) -> web.Response:
        fw = self.ctx.firmware
        if fw is None:
            raise web.HTTPConflict(text="network boot is disabled")
        body = await req.json()
        choice = str(body.get("name", ""))
        names = {a["name"] for a in fw.available()} | {a["file"] for a in fw.available()}
        if choice not in names:
            raise web.HTTPBadRequest(text="unknown firmware")
        fw.select(choice)
        return web.json_response({"ok": True, "selected": choice})

    # -- playlists ---------------------------------------------------------
    async def playlist_create(self, req: web.Request) -> web.Response:
        body = await _body(req)
        pid = await self._run(self.lib.create_playlist, str(body.get("name", "")), _ids(body))
        return web.json_response(await self._run(self.lib.playlist, pid))

    async def _playlist_change(self, fn, *args):
        try:
            return await self._run(fn, *args)
        except KeyError:
            raise web.HTTPNotFound(text="no such playlist")
        except PermissionError as exc:
            raise web.HTTPConflict(text=str(exc))

    async def playlist_update(self, req: web.Request) -> web.Response:
        pid = int(req.match_info["id"])
        body = await _body(req)
        if "name" in body:
            await self._playlist_change(self.lib.rename_playlist, pid, str(body["name"]))
        if "track_ids" in body:
            await self._playlist_change(self.lib.set_playlist_tracks, pid, _ids(body))
        return await self.playlist(req)

    async def playlist_add(self, req: web.Request) -> web.Response:
        pid = int(req.match_info["id"])
        body = await _body(req)
        await self._playlist_change(self.lib.add_to_playlist, pid, _ids(body))
        return web.json_response(await self._run(self.lib.playlist, pid))

    async def playlist_delete(self, req: web.Request) -> web.Response:
        await self._playlist_change(self.lib.delete_playlist, int(req.match_info["id"]))
        return web.json_response({"ok": True})

    # -- history -----------------------------------------------------------
    @staticmethod
    def _limit(req: web.Request, default: int) -> int:
        try:
            return max(1, min(1000, int(req.query.get("limit", default))))
        except ValueError:
            raise web.HTTPBadRequest(text="limit must be a number")

    async def history_recent(self, req: web.Request) -> web.Response:
        rows = await self._run(self.lib.recently_played, self._limit(req, 100))
        return web.json_response([{**_track_json(t), "played_at": t["last_played"]} for t in rows])

    async def history_top(self, req: web.Request) -> web.Response:
        rows = await self._run(self.lib.most_played, self._limit(req, 25))
        return web.json_response([{**_track_json(t), "play_count": t["play_count"]} for t in rows])

    async def history_log(self, req: web.Request) -> web.Response:
        rows = await self._run(self.lib.history, self._limit(req, 100))
        return web.json_response([{**_track_json(t), "played_at": t["played_at"], "device": t["device"]}
                                  for t in rows])

    # -- the browser's own player ---------------------------------------------
    @staticmethod
    def _client_id(value) -> str:
        return "web:" + "".join(ch for ch in str(value or "browser") if ch.isalnum() or ch in "-_")[:40]

    async def progress(self, req: web.Request) -> web.Response:
        """The web interface's player reports what it plays, for the play
        history and podcast positions."""
        body = await _body(req)
        tracker = getattr(self.ctx, "tracker", None)
        t = await self._run(self.lib.track, _int_field(body, "track_id"))
        if tracker is not None and t:
            st = str(body.get("st") or "play")
            if st not in ("play", "pause", "stop", "buffer"):
                st = "play"
            name = str(body.get("name") or "Web browser")[:40]
            tracker.report(self._client_id(body.get("client")), name, track_state(t), st,
                           max(0, _int_field(body, "pos_ms")))
        return web.json_response({"ok": True})

    async def track_start(self, req: web.Request) -> web.Response:
        """Where the browser should start a track (podcast resume)."""
        t = await self._run(self.lib.track, int(req.match_info["id"]))
        if not t:
            raise web.HTTPNotFound()
        tracker = getattr(self.ctx, "tracker", None)
        start = tracker.start_position(self._client_id(req.query.get("client")), t) if tracker else 0
        return web.json_response({"start_ms": start})

    # -- podcasts --------------------------------------------------------------
    def _pods(self):
        pods = getattr(self.ctx, "podcasts", None)
        if pods is None:
            raise web.HTTPNotFound(text="podcasts are switched off")
        return pods

    async def podcasts(self, req: web.Request) -> web.Response:
        return web.json_response(await self._run(self._pods().shows))

    async def podcast(self, req: web.Request) -> web.Response:
        p = await self._run(self._pods().show, int(req.match_info["id"]))
        if not p:
            raise web.HTTPNotFound(text="no such podcast")
        return web.json_response(p)

    async def podcast_subscribe(self, req: web.Request) -> web.Response:
        body = await _body(req)
        try:
            pid = await self._pods().subscribe(str(body.get("url", "")), _int_field(body, "keep", 3) or 3)
        except PodcastError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        except Exception as exc:  # network trouble of any kind
            raise web.HTTPBadGateway(text=f"the feed couldn't be fetched: {exc}")
        return web.json_response(await self._run(self._pods().show, pid))

    async def podcast_update(self, req: web.Request) -> web.Response:
        pid = int(req.match_info["id"])
        body = await _body(req)
        if "keep" in body:
            await self._run(self._pods().set_keep, pid, _int_field(body, "keep"))
        return await self.podcast(req)

    async def podcast_delete(self, req: web.Request) -> web.Response:
        await self._run(self._pods().unsubscribe, int(req.match_info["id"]))
        return web.json_response({"ok": True})

    async def podcast_refresh(self, req: web.Request) -> web.Response:
        await self._pods().refresh(int(req.match_info["id"]))
        return await self.podcast(req)

    async def podcasts_refresh_all(self, req: web.Request) -> web.Response:
        asyncio.create_task(self._pods().refresh_all())
        return web.json_response({"ok": True})

    async def podcasts_recent(self, req: web.Request) -> web.Response:
        tracks = await self._run(self._pods().recent_episode_tracks, self._limit(req, 30))
        return web.json_response([{**_track_json(t), "episode_id": t["episode_id"], "position_ms": t["position_ms"],
                                   "played": t["played"], "published": t["published"]} for t in tracks])

    async def podcasts_opml(self, req: web.Request) -> web.Response:
        shows = await self._run(self._pods().shows)
        return web.Response(text=make_opml(shows), content_type="text/x-opml", headers={
            "Content-Disposition": 'attachment; filename="rioserver-podcasts.opml"'})

    async def podcasts_import(self, req: web.Request) -> web.Response:
        data = await req.read()
        try:
            urls = parse_opml(data)
        except PodcastError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        added, failed = 0, []
        for url in urls[:200]:
            try:
                await self._pods().subscribe(url)
                added += 1
            except Exception as exc:
                failed.append({"url": url, "error": str(exc)[:200]})
        return web.json_response({"added": added, "failed": failed})

    async def _episode(self, fn, *args):
        try:
            return await self._run(fn, *args)
        except KeyError:
            raise web.HTTPNotFound(text="no such episode")
        except PodcastError as exc:
            raise web.HTTPConflict(text=str(exc))

    async def episode_update(self, req: web.Request) -> web.Response:
        eid = int(req.match_info["id"])
        body = await _body(req)
        played = bool(body["played"]) if "played" in body else None
        position = _int_field(body, "position_ms") if "position_ms" in body else None
        await self._episode(self._pods().mark, eid, played, position)
        return web.json_response(await self._run(self._pods().episode, eid))

    async def episode_download(self, req: web.Request) -> web.Response:
        eid = int(req.match_info["id"])
        await self._episode(self._pods().download, eid)
        return web.json_response(await self._run(self._pods().episode, eid))

    async def episode_delete(self, req: web.Request) -> web.Response:
        eid = int(req.match_info["id"])
        await self._episode(self._pods().delete_download, eid)
        return web.json_response(await self._run(self._pods().episode, eid))

    async def podcast_art(self, req: web.Request) -> web.Response:
        path = self._pods().cover_path(int(req.match_info["id"]))
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})

    # -- alarms ------------------------------------------------------------------
    def _alarms(self):
        clock = getattr(self.ctx, "alarms", None)
        if clock is None:
            raise web.HTTPNotFound(text="alarms aren't available")
        return clock

    async def alarms(self, req: web.Request) -> web.Response:
        return web.json_response(self._alarms().listing())

    async def alarm_add(self, req: web.Request) -> web.Response:
        try:
            self._alarms().add(await _body(req))
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response(self._alarms().listing())

    async def alarm_update(self, req: web.Request) -> web.Response:
        try:
            self._alarms().update(req.match_info["id"], await _body(req))
        except KeyError:
            raise web.HTTPNotFound(text="no such alarm")
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response(self._alarms().listing())

    async def alarm_delete(self, req: web.Request) -> web.Response:
        self._alarms().delete(req.match_info["id"])
        return web.json_response(self._alarms().listing())

    async def alarm_test(self, req: web.Request) -> web.Response:
        a = self._alarms().get(req.match_info["id"])
        if a is None:
            raise web.HTTPNotFound(text="no such alarm")
        try:
            await self.fire_alarm({**a, "fade": min(a["fade"], 1)})
        except LookupError as exc:
            raise web.HTTPConflict(text=str(exc))
        return web.json_response({"ok": True})

    def _alarm_tracks(self, source: dict) -> list[dict]:
        kind, sid = source["type"], source.get("id") or 0
        if kind == "playlist":
            return self.lib.playlist_tracks(sid)
        if kind == "album":
            return self.lib.tracks(album_id=sid)
        if kind == "artist":
            return self._ng.tracks_for(f"ar:{sid}/all")
        if kind == "top25":
            return self.lib.most_played(25)
        if kind == "played":
            return self.lib.recently_played(100)
        if kind == "podcast":
            pods = getattr(self.ctx, "podcasts", None)
            eps = pods.episode_tracks(sid) if pods else []
            fresh = [t for t in eps if not t["played"]]
            return (fresh or eps)[:1]
        return self.lib.tracks()  # shuffle the whole library

    async def fire_alarm(self, alarm: dict) -> None:
        """Start an alarm: silence, play, then fade up to its volume."""
        reg = self.ctx.receivers
        r = reg.get(alarm["receiver"])
        if r is None or (isinstance(r, Receiver) and r.firmware != "ng"):
            raise LookupError("the alarm's receiver isn't available")
        tracks = await self._run(self._alarm_tracks, alarm["source"])
        if not tracks:
            raise LookupError("there's nothing to play for this alarm")
        shuffle = alarm.get("shuffle") or alarm["source"]["type"] == "shuffle"
        index = 0
        if shuffle:
            import random
            index = random.randrange(len(tracks))
        target = int(alarm.get("volume") or 40)
        fade = max(0.0, float(alarm.get("fade") or 0)) * 60
        self._stop_fade(r)
        reg.command(r, "vol", 1 if fade else target)
        reg.play(r, tracks, index, bool(shuffle))
        if fade:
            self._faders[r.key] = asyncio.create_task(self._fade_in(r, target, fade))

    async def _fade_in(self, r, target: int, seconds: float) -> None:
        reg = self.ctx.receivers
        try:
            for i in range(1, FADE_STEPS + 1):
                await asyncio.sleep(seconds / FADE_STEPS)
                reg.command(r, "vol", max(1, round(target * i / FADE_STEPS)))
        except asyncio.CancelledError:
            return
        finally:
            if self._faders.get(r.key) is asyncio.current_task():
                del self._faders[r.key]

    def _stop_fade(self, r) -> None:
        task = self._faders.pop(r.key, None)
        if task:
            task.cancel()

    # -- this browser as a receiver ---------------------------------------------
    async def player_ws(self, req: web.Request) -> web.WebSocketResponse:
        """A browser in receiver mode (see webplayer.py)."""
        client = "".join(ch for ch in req.query.get("id", "") if ch.isalnum() or ch in "-_")[:40]
        if not client:
            raise web.HTTPBadRequest(text="id is required")
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        reg = self.ctx.receivers
        pid = f"web-{client}"
        player = reg.get(pid)
        if not isinstance(player, BrowserPlayer):
            player = BrowserPlayer(pid, asyncio.get_running_loop())
            reg.add(player)
        player.attach(ws, req.remote or "", device_model(req.headers.get("User-Agent", "")))
        log.info("%s is a receiver now (%s)", player.name, player.model)
        await ws.send_str(json.dumps({"type": "welcome", "id": pid}))
        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "hello":
                    player.volume_supported = data.get("volume") is not False
                    name = str(data.get("name") or "").strip()
                    if name and name != player.name:
                        reg.rename(player, name)
                else:
                    player.handle(data)
        finally:
            player.detach(ws)
            log.info("%s disconnected", player.name)
        return ws

    async def receiver_forget(self, req: web.Request) -> web.Response:
        """Remove a browser receiver from the list (when it's offline)."""
        r = self.ctx.receivers.get(req.match_info["id"])
        if not isinstance(r, BrowserPlayer):
            raise web.HTTPConflict(text="only browsers used as receivers can be forgotten")
        if r.online:
            raise web.HTTPConflict(text="it is still connected; stop receiver mode on that device instead")
        self.ctx.receivers.remove(r.id)
        return web.json_response({"ok": True})
