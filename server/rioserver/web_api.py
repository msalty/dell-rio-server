"""JSON API behind the web interface, plus album art and browser streaming."""

from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path

from aiohttp import web

from . import ids
from .firmware import FirmwareStore
from .rio_classic import send_audio

log = logging.getLogger(__name__)

COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall")
COVER_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
RECEIVER_COMMANDS = {"pause", "resume", "toggle", "stop", "next", "prev", "vol", "seek", "msg"}


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


def find_cover_file(directory: Path) -> Path | None:
    try:
        files = list(directory.iterdir())
    except OSError:
        return None
    images = [f for f in files if f.suffix.lower() in COVER_EXTS and f.is_file()]
    for name in COVER_NAMES:
        for f in images:
            if f.stem.lower() == name:
                return f
    return images[0] if len(images) == 1 else None


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
            mime = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(
                cover.suffix.lower(), "image/jpeg")
            return cover.read_bytes(), mime
        except OSError:
            return None
    return None


class WebAPI:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lib = ctx.library

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
            web.get("/api/stream/{id:\\d+}", self.stream),
            web.get("/api/receivers", self.receivers),
            web.post("/api/receivers/{ip}/play", self.receiver_play),
            web.post("/api/receivers/{ip}/command", self.receiver_command),
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
        r = self.ctx.receivers.get(req.match_info["ip"])
        if r is None:
            raise web.HTTPNotFound(text="unknown receiver")
        if r.firmware != "ng":
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
        r.commands.append(("play", node, index, shuffle))
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
        r.commands.append(tuple(args))
        return web.json_response({"ok": True})

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
