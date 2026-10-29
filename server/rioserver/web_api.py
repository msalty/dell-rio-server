"""JSON API behind the web interface, plus album art and browser streaming."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from pathlib import Path

from aiohttp import web

from . import ids
from .firmware import FirmwareStore
from .receivers import Receiver
from .rio_classic import send_audio
from .rio_ng import NGProtocol, NodeError

log = logging.getLogger(__name__)

COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall")
COVER_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
ARTIST_NAMES = ("artist", "artist-photo", "band")
RECEIVER_COMMANDS = {"pause", "resume", "toggle", "stop", "next", "prev", "vol", "seek", "msg"}
SLEEP_FADE = 20  # seconds over which a sleep timer fades the volume out


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
    }


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
            "receivers": [r.to_json() for r in self.ctx.receivers.all()],
        })

    async def rescan(self, req: web.Request) -> web.Response:
        full = req.query.get("full") == "1"
        self.ctx.watcher.request_scan(full=full)
        return web.json_response({"ok": True, "full": full})

    async def artists(self, req: web.Request) -> web.Response:
        rows = await self._run(self.lib.artists)
        return web.json_response([
            {"id": a["id"], "name": a["name"], "album_count": a["album_count"],
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
        return web.json_response({"total": len(tracks),
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
        body = await req.json()
        index = int(body.get("index", 0))
        shuffle = 1 if body.get("shuffle") else 0
        if "track_ids" in body:
            track_ids = [int(x) for x in body["track_ids"]][:10000]
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
        else:
            try:
                tracks = await self._run(self._ng.tracks_for, node)
            except NodeError:
                raise web.HTTPBadRequest(text="nothing to play")
            if not tracks:
                raise web.HTTPNotFound(text="nothing to play")
            r.play(tracks, index, bool(shuffle))
        return web.json_response({"ok": True, "node": node})

    async def receiver_command(self, req: web.Request) -> web.Response:
        r = self._receiver(req)
        body = await req.json()
        cmd = str(body.get("cmd", ""))
        if cmd not in RECEIVER_COMMANDS:
            raise web.HTTPBadRequest(text="unknown command")
        args = [cmd]
        if "value" in body:
            args.append(str(body["value"]).replace("\t", " ")[:200])
        self.ctx.receivers.command(r, *args)
        return web.json_response({"ok": True})

    async def receiver_name(self, req: web.Request) -> web.Response:
        r = self.ctx.receivers.get(req.match_info["id"])
        if r is None:
            raise web.HTTPNotFound(text="unknown receiver")
        body = await req.json()
        self.ctx.receivers.rename(r, str(body.get("name", "")))
        return web.json_response(r.to_json())

    async def receiver_sleep(self, req: web.Request) -> web.Response:
        """Sleep timer: fade out, pause, and put the volume back for next time."""
        r = self._receiver(req)
        body = await req.json()
        try:
            minutes = float(body.get("minutes", 0))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="minutes must be a number")
        old = self._sleepers.pop(r.id, None)
        if old:
            old.cancel()
        r.sleep_at = None
        if minutes > 0:
            delay = min(minutes, 24 * 60) * 60
            r.sleep_at = time.time() + delay
            self._sleepers[r.id] = asyncio.create_task(self._sleep(r, delay))
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
            if self._sleepers.get(r.id) is asyncio.current_task():
                del self._sleepers[r.id]
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
