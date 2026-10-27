"""The original Rio Receiver HTTP protocol, as spoken by the stock firmware.

Reconstructed from JReceiver (Reed Esau, BSD licence) and RioPlay (David
Flowerday, GPL), both of which talked to real receivers:

  GET /tags                     list of tag names
  GET /tags/<fid>               binary tag record for a track or playlist
  GET /query?<field>=<prefix>   menu entries ("matches=" + "n=count,0,0:Name")
  GET /results?<field>=<value>  tracks matching a menu entry
                                 _extended=1 -> text "fid=TTitle"
                                 _extended=0 -> binary (fid, size)
                                 _extended=2 -> binary (fid, size, offset)
  GET /list/<fid>?_extended=2   binary track records of a playlist or track
  GET /content/<fid>            MP3 data (with Range: bytes=a-b) or, for a
                                playlist, its track list
  GET /content/100?_extended=1  all playlists, "fid=PName"
  GET /favourites/all           (empty)
  GET /layout/en_UK/...         display layouts for the stock player

Fields are artist, source (album), genre and title. Unlike the original
server, results are returned in album/track order rather than by title.
"""

from __future__ import annotations

import logging
import struct

from aiohttp import web

from . import ids
from .ids import device_bytes
from .library import compile_prefix

log = logging.getLogger(__name__)

SERVER_HEADER = "Mercury-Content-Server/0.11"
RESULT_LIMIT = 10000

# Binary tag keys (see JReceiver RioHostTags)
KEY_FID, KEY_TITLE, KEY_ARTIST, KEY_ALBUM, KEY_YEAR, KEY_COMMENT = 0, 1, 2, 3, 4, 5
KEY_LENGTH, KEY_TYPE, KEY_PATH, KEY_GENRE, KEY_BITRATE, KEY_PLAYLIST = 6, 7, 8, 9, 10, 11
KEY_CODEC, KEY_OFFSET, KEY_DURATION, KEY_TRACKNR = 12, 13, 14, 15

TAG_NAMES = [
    "fid", "title", "artist", "source", "year", "comment", "length", "type", "path",
    "genre", "bitrate", "playlist", "codec", "offset", "duration", "tracknr",
]
FIELDS = ("artist", "source", "genre", "title")


def _text(body: str) -> web.Response:
    return web.Response(body=device_bytes(body, keep_layout=True), content_type="text/plain",
                        charset="iso-8859-1", headers={"Server": SERVER_HEADER})


def _binary(body: bytes) -> web.Response:
    return web.Response(body=body, content_type="application/octet-stream",
                        headers={"Server": SERVER_HEADER})


def _int_param(req: web.Request, name: str, default: int) -> int:
    try:
        return int(req.query.get(name, default))
    except ValueError:
        return default


def _range(req: web.Request, total: int) -> tuple[int, int]:
    begin = max(0, _int_param(req, "_begin", 0))
    end = _int_param(req, "_end", -1)
    if end < 0 or end >= total:
        end = total - 1
    end = min(end, begin + RESULT_LIMIT - 1)
    return begin, end


def _field_and_value(req: web.Request) -> tuple[str | None, str]:
    for f in FIELDS:
        if f in req.query:
            return f, req.query.get(f, "")
    return None, ""


def _pstr(key: int, value) -> bytes:
    raw = device_bytes(str(value))[:255]
    return bytes([key, len(raw)]) + raw


def track_size(t: dict) -> int:
    return max(0, t["size"] - t["data_offset"])


class ClassicProtocol:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lib = ctx.library

    def routes(self) -> list:
        return [
            web.get("/tags", self.tags),
            web.get("/tags/{fid}", self.tags),
            web.get("/query", self.query),
            web.get("/results", self.results),
            web.get("/list/{fid}", self.list),
            web.get("/content/{fid}", self.content),
            web.get("/favourites{tail:.*}", self.favourites),
            web.get("/layout/{tail:.*}", self.layout),
            web.get("/descriptor.xml", self.descriptor),
        ]

    def _seen(self, req: web.Request) -> None:
        if req.remote:
            r = self.ctx.receivers.touch(req.remote, stage="running")
            if r.firmware == "unknown":
                r.firmware = "stock"

    # -- helpers --------------------------------------------------------
    def tracks_for(self, field: str, value: str) -> list[dict]:
        """Tracks matching an exact menu entry (or everything if empty)."""
        lib = self.lib
        if not value:
            if field == "title":
                return lib.tracks()
            if field == "source":
                return sorted(lib.tracks(), key=lambda t: (t["album"].casefold(), t["album_artist"].casefold(),
                                                           t["disc_no"] or 1, t["track_no"] or 9999))
            return sorted(lib.tracks(), key=lambda t: (t["album_artist"].casefold(),
                                                       t["album_year"] or 9999, t["album"].casefold(),
                                                       t["disc_no"] or 1, t["track_no"] or 9999))
        value = self._strip_count(field, value)
        if field == "artist":
            a = lib.artist_by_name(value)
            if a:
                return lib.tracks(artist_id=a["id"])
            # Fall back to track artist (e.g. guest artists on compilations).
            return [t for t in lib.tracks() if t["artist"].casefold() == value.casefold()]
        if field == "source":
            out = []
            for al in lib.albums_by_title(value):
                out.extend(lib.tracks(album_id=al["id"]))
            return out
        if field == "genre":
            g = lib.genre_by_name(value)
            return lib.tracks(genre_id=g["id"]) if g else []
        if field == "title":
            return [t for t in lib.tracks() if t["title"].casefold() == value.casefold()]
        return []

    def _strip_count(self, field: str, value: str) -> str:
        """Accept "Name (12)" as sent back by players that saw counts."""
        if value.endswith(")") and " (" in value:
            base = value[: value.rindex(" (")]
            exists = {
                "artist": lambda v: self.lib.artist_by_name(v),
                "source": lambda v: self.lib.albums_by_title(v),
                "genre": lambda v: self.lib.genre_by_name(v),
            }.get(field)
            if exists and not exists(value) and exists(base):
                return base
        return value

    def menu_entries(self, field: str, prefix: str) -> list[tuple[str, int]]:
        lib = self.lib
        rx = prefix or None
        if field == "artist":
            return [(a["name"], a["track_count"]) for a in lib.artists(rx)]
        if field == "source":
            seen: dict[str, int] = {}
            for al in sorted(lib.albums(prefix_regex=rx), key=lambda a: a["title"].casefold()):
                seen[al["title"]] = seen.get(al["title"], 0) + al["track_count"]
            return list(seen.items())
        if field == "genre":
            return [(g["name"], g["track_count"]) for g in lib.genres(rx)]
        if field == "title":
            rxc = compile_prefix(prefix)
            return [(t["title"], 1) for t in lib.tracks() if rxc is None or rxc.match(t["title"])]
        return []

    # -- handlers -------------------------------------------------------
    async def descriptor(self, req: web.Request) -> web.Response:
        xml = (
            '<?xml version="1.0"?>\n<root xmlns="urn:schemas-upnp-org:device-1-0">'
            "<specVersion><major>1</major><minor>0</minor></specVersion><device>"
            "<deviceType>urn:empeg-com:device:RioMusicServer:1</deviceType>"
            "<friendlyName>RioServer NG</friendlyName><manufacturer>RioServer NG</manufacturer>"
            "<modelName>RioServer NG</modelName>"
            "<UDN>uuid:1D274DB0-F053-11d3-BF72-0050DA689B2F</UDN></device></root>\n"
        )
        return web.Response(text=xml, content_type="text/xml", headers={"Server": SERVER_HEADER})

    async def tags(self, req: web.Request) -> web.Response:
        self._seen(req)
        raw = req.match_info.get("fid")
        if raw is None:
            return _text("".join(n + "\n" for n in TAG_NAMES))
        fid = ids.parse_hex(raw)
        kind = ids.decode_fid(fid) if fid is not None else None
        if kind is None:
            raise web.HTTPNotFound()
        what, oid = kind
        if what == "track":
            t = self.lib.track(oid)
            if not t:
                raise web.HTTPNotFound()
            return _binary(self.track_tag(fid, t))
        if what == "all":
            title, count = "All Tracks", len(self.playlist_entries())
        elif what == "playlist":
            p = self.lib.playlist(oid)
            if not p:
                raise web.HTTPNotFound()
            title, count = p["name"], len(self.lib.playlist_tracks(oid))
        else:
            al = self.lib.album(oid)
            if not al:
                raise web.HTTPNotFound()
            title, count = f"{al['artist']} - {al['title']}", len(self.lib.tracks(album_id=oid))
        body = (_pstr(KEY_FID, fid) + _pstr(KEY_TITLE, title) + _pstr(KEY_TYPE, "playlist")
                + _pstr(KEY_LENGTH, count * 4) + b"\xff")
        return _binary(body)

    @staticmethod
    def track_tag(fid: int, t: dict) -> bytes:
        bitrate = t["bitrate"] or 128
        parts = [
            _pstr(KEY_ARTIST, t["artist"]),
            _pstr(KEY_BITRATE, f"{'vs' if t['vbr'] else 'fs'}{bitrate}"),
            _pstr(KEY_CODEC, "mp3"),
            _pstr(KEY_DURATION, t["duration_ms"]),
            _pstr(KEY_FID, fid),
            _pstr(KEY_GENRE, t["genre"]),
            _pstr(KEY_LENGTH, track_size(t)),
            _pstr(KEY_PATH, f"/content/{fid:x}"),
            _pstr(KEY_TITLE, t["title"]),
            _pstr(KEY_ALBUM, t["album"]),
            _pstr(KEY_TRACKNR, t["track_no"] or ""),
            _pstr(KEY_YEAR, t["year"] or t["album_year"] or ""),
            _pstr(KEY_OFFSET, 0),
            _pstr(KEY_TYPE, "tune"),
        ]
        return b"".join(parts) + b"\xff"

    async def query(self, req: web.Request) -> web.Response:
        self._seen(req)
        field, prefix = _field_and_value(req)
        if field is None:
            raise web.HTTPBadRequest(text="invalid target")
        entries = self.menu_entries(field, prefix)
        if not entries:
            return _text("matches=\n")
        begin, end = _range(req, len(entries))
        lines = ["matches=\n"]
        show = self.ctx.config.classic_show_counts
        for i in range(begin, end + 1):
            name, count = entries[i]
            label = f"{name} ({count})" if show else name
            lines.append(f"{i}={count},0,0:{label}\r\n")
        return _text("".join(lines))

    async def results(self, req: web.Request) -> web.Response:
        self._seen(req)
        field, value = _field_and_value(req)
        if field is None:
            raise web.HTTPBadRequest(text="invalid target")
        tracks = self.tracks_for(field, value)
        return self._track_list(req, tracks, _int_param(req, "_extended", 0))

    def _track_list(self, req: web.Request, tracks: list[dict], extended: int) -> web.Response:
        if not tracks:
            return _text("") if extended == 1 else _binary(b"")
        begin, end = _range(req, len(tracks))
        sel = tracks[begin:end + 1]
        if extended == 1:
            return _text("".join(f"{ids.track_fid(t['id']):x}=T{t['title']}\r\n" for t in sel))
        if extended == 2:
            return _binary(b"".join(struct.pack("<III", ids.track_fid(t["id"]), track_size(t), 0) for t in sel))
        return _binary(b"".join(struct.pack("<II", ids.track_fid(t["id"]), track_size(t)) for t in sel))

    def playlist_entries(self) -> list[tuple[int, str, int]]:
        out = [(ids.playlist_fid(p["id"]), p["name"], p["track_count"]) for p in self.lib.playlists()]
        if self.ctx.config.extra.get("classic_album_playlists", True):
            for al in sorted(self.lib.albums(), key=lambda a: (a["artist"].casefold(), a["year"] or 9999)):
                out.append((ids.album_fid(al["id"]), f"{al['artist']} - {al['title']}", al["track_count"]))
        return out

    def playlist_tracks(self, fid: int) -> list[dict] | None:
        kind = ids.decode_fid(fid)
        if kind is None:
            return None
        what, oid = kind
        if what == "playlist":
            return self.lib.playlist_tracks(oid)
        if what == "album":
            return self.lib.tracks(album_id=oid)
        if what == "track":
            t = self.lib.track(oid)
            return [t] if t else []
        return None

    async def list(self, req: web.Request) -> web.Response:
        self._seen(req)
        fid = ids.parse_hex(req.match_info["fid"])
        if fid == ids.ALL_PLAYLISTS:
            albums = self.lib.albums()
            tracks = self.lib.tracks(album_id=albums[0]["id"]) if albums else []
        else:
            tracks = self.playlist_tracks(fid) if fid is not None else None
            if tracks is None:
                raise web.HTTPNotFound()
        return self._track_list(req, tracks, 2)

    async def content(self, req: web.Request) -> web.StreamResponse:
        self._seen(req)
        fid = ids.parse_hex(req.match_info["fid"])
        kind = ids.decode_fid(fid) if fid is not None else None
        if kind is None:
            raise web.HTTPNotFound()
        what, oid = kind
        extended = _int_param(req, "_extended", 0)
        if what == "all":
            entries = self.playlist_entries()
            begin, end = _range(req, len(entries)) if entries else (0, -1)
            sel = entries[begin:end + 1]
            if extended == 1:
                return _text("".join(f"{f:x}=P{name}\r\n" for f, name, _ in sel))
            return _binary(b"".join(struct.pack("<I", f) for f, _, _ in sel))
        if what in ("playlist", "album"):
            tracks = self.playlist_tracks(fid) or []
            if extended == 1:
                return self._track_list(req, tracks, 1)
            begin, end = _range(req, len(tracks)) if tracks else (0, -1)
            return _binary(b"".join(struct.pack("<I", ids.track_fid(t["id"])) for t in tracks[begin:end + 1]))
        t = self.lib.track(oid)
        if not t:
            raise web.HTTPNotFound()
        return await send_audio(req, self.lib.track_path(t), t["data_offset"], track_size(t))

    async def favourites(self, req: web.Request) -> web.Response:
        self._seen(req)
        return _text("")

    async def layout(self, req: web.Request) -> web.Response:
        self._seen(req)
        tail = req.match_info["tail"]
        locale, _, name = tail.partition("/")
        if name == "index":
            return _text("".join(f"/layout/{locale}/{key}={label}\r\n" for key, label, _ in LAYOUTS))
        for key, _, words in LAYOUTS:
            if name == key:
                return _binary(struct.pack(f"<{len(words)}I", *words))
        raise web.HTTPNotFound()


async def send_audio(req: web.Request, path, data_offset: int, size: int,
                     rio_ranges: bool = True) -> web.StreamResponse:
    """Serve MP3 data, honouring Range requests.

    For receivers (rio_ranges=True) a ranged reply uses the Content-Range
    format JReceiver sent to the stock player ("bytes=a-b/*"); browsers get
    the standard "bytes a-b/size". Without a Range header the whole file is
    streamed (what RioPlay and the RioServer NG firmware ask for). Offsets
    are relative to data_offset, i.e. the first byte after any ID3v2 tag.
    """
    rng = req.headers.get("Range")
    start, stop = 0, size - 1
    partial = False
    if rng and "=" in rng:
        spec = rng.split("=", 1)[1].split(",")[0].strip()
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
            return web.Response(status=416, headers={"Content-Range": f"bytes */{size}",
                                                     "Server": SERVER_HEADER})
        stop = min(stop, size - 1)
    length = stop - start + 1
    headers = {"Server": SERVER_HEADER, "Content-Type": "audio/mpeg",
               "Content-Length": str(length), "Accept-Ranges": "bytes"}
    status = 200
    if partial:
        status = 206
        complete = stop == size - 1
        if rio_ranges:
            headers["Content-Range"] = f"bytes={start}-{stop}/" + (str(size) if complete else "*")
        else:
            headers["Content-Range"] = f"bytes {start}-{stop}/{size}"
    resp = web.StreamResponse(status=status, headers=headers)
    await resp.prepare(req)
    if req.method == "HEAD":
        return resp
    try:
        with open(path, "rb") as fh:
            fh.seek(data_offset + start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                await resp.write(chunk)
                remaining -= len(chunk)
    except (ConnectionResetError, OSError) as exc:
        log.debug("audio stream to %s ended: %s", req.remote, exc)
    return resp


# Display layouts for the stock player (decoded by David Schuetz, from JReceiver).
LAYOUTS = [
    ("all_info", "All Info", [
        0x02, 0x0C, 0x00, 0x80, 0x09, 0x04, 0x06000002,
        0x02, 0x00, 0x09, 0x80, 0x14, 0x04, 0x00000000,
        0x06, 0x00, 0x13, 0x80, 0x14, 0x08, 0x00, 0x0F,
        0x02, 0x00, 0x15, 0x80, 0x20, 0x04, 0x01000000,
        0x02, 0x00, 0x20, 0x80, 0x2A, 0x04, 0x03000000,
        0x03, 0x3E, 0x2A, 0x80, 0x40, 0x04, 0x00030002,
        0x09, 0x00, 0x33, 0x3E, 0x3B, 0x04, 0x00000000,
        0x02, 0x00, 0x3B, 0x3E, 0x40, 0x04, 0x05010000,
        0x09, 0x26, 0x31, 0x3E, 0x40, 0x04, 0x00010000,
        0x09, 0x26, 0x3B, 0x50, 0x40, 0x04, 0x00020000,
        0x04, 0x00, 0x00, 0x0C, 0x08, 0x04, 0x00020000]),
    ("with_volume", "All, With Volume", [
        0x02, 0x0C, 0x00, 0x80, 0x09, 0x04, 0x06000002,
        0x02, 0x00, 0x09, 0x80, 0x34, 0x04, 0x00000000,
        0x06, 0x00, 0x13, 0x80, 0x14, 0x08, 0x00, 0x0F,
        0x02, 0x00, 0x15, 0x80, 0x20, 0x04, 0x01000000,
        0x02, 0x00, 0x20, 0x80, 0x2A, 0x04, 0x03000000,
        0x03, 0x3E, 0x2A, 0x80, 0x40, 0x04, 0x00030002,
        0x07, 0x00, 0x33, 0x40, 0x40, 0x04, 0x00000000,
        0x09, 0x1F, 0x29, 0x3E, 0x31, 0x04, 0x00010000,
        0x09, 0x00, 0x2B, 0x1A, 0x31, 0x04, 0x00020000,
        0x04, 0x00, 0x00, 0x0C, 0x08, 0x04, 0x00020000]),
    ("remaining", "Remaining Time", [
        0x02, 0x0C, 0x00, 0x80, 0x09, 0x04, 0x06000002,
        0x02, 0x00, 0x09, 0x80, 0x14, 0x04, 0x00000000,
        0x06, 0x00, 0x13, 0x80, 0x14, 0x08, 0x00, 0x0F,
        0x02, 0x00, 0x15, 0x80, 0x20, 0x04, 0x01000000,
        0x02, 0x00, 0x20, 0x80, 0x2A, 0x04, 0x03000000,
        0x03, 0x40, 0x2A, 0x80, 0x40, 0x04, 0x01030002,
        0x09, 0x00, 0x33, 0x40, 0x3B, 0x04, 0x00000000,
        0x02, 0x00, 0x3B, 0x40, 0x40, 0x04, 0x05010000,
        0x09, 0x30, 0x31, 0x40, 0x40, 0x04, 0x00010000,
        0x09, 0x30, 0x3B, 0x50, 0x40, 0x04, 0x00020000,
        0x04, 0x00, 0x00, 0x0C, 0x08, 0x04, 0x00020000]),
    ("big_title", "Big Title", [
        0x02, 0x00, 0x00, 0x80, 0x12, 0x04, 0x00040000,
        0x02, 0x00, 0x14, 0x80, 0x1F, 0x04, 0x01000000,
        0x03, 0x20, 0x2A, 0x60, 0x40, 0x04, 0x00030002,
        0x09, 0x00, 0x28, 0x20, 0x31, 0x04, 0x00000000,
        0x09, 0x70, 0x31, 0x80, 0x40, 0x04, 0x00010002,
        0x09, 0x60, 0x28, 0x80, 0x31, 0x04, 0x00020002,
        0x04, 0x00, 0x38, 0x0C, 0x40, 0x04, 0x00020000]),
    ("scope", "Scope", [
        0x02, 0x00, 0x00, 0x80, 0x0B, 0x04, 0x00000000,
        0x02, 0x00, 0x0B, 0x80, 0x16, 0x04, 0x01000000,
        0x02, 0x00, 0x16, 0x80, 0x20, 0x04, 0x03000000,
        0x03, 0x40, 0x2A, 0x80, 0x40, 0x04, 0x00030002,
        0x09, 0x71, 0x20, 0x80, 0x29, 0x04, 0x00010000,
        0x08, 0x00, 0x20, 0x40, 0x40, 0x00]),
    ("inverse_scope", "Inverse Scope", [
        0x02, 0x01, 0x01, 0x7F, 0x0C, 0x04, 0x00000000,
        0x02, 0x01, 0x0C, 0x7F, 0x17, 0x04, 0x01000000,
        0x02, 0x01, 0x17, 0x7F, 0x21, 0x04, 0x03000000,
        0x03, 0x40, 0x2A, 0x7F, 0x3F, 0x04, 0x00030002,
        0x09, 0x71, 0x20, 0x80, 0x29, 0x04, 0x00010000,
        0x08, 0x01, 0x21, 0x40, 0x3F, 0x00,
        0x06, 0x00, 0x00, 0x80, 0x40, 0x08, 0x02, 0x00]),
]
