"""The protocol spoken by the RioServer NG receiver firmware.

The firmware is deliberately thin: the server decides what the menus contain,
so the browsing experience can be improved without reflashing anything.
All responses are Latin-1 text: a "RNG 1" header line followed by
tab-separated records.

  GET /ng/menu/<node>?b=<begin>&n=<count>
      title  <title>
      total  <number of items>
      begin  <index of first item returned>
      i      <kind> <ref> <label> <detail>     (one per item)
        kind M = submenu (ref is a node), T = track (ref is a hex fid; picking
        it plays this menu's tracks starting there), S = shuffle the node in ref
  GET /ng/queue/<node>?shuffle=0|1&first=<index>
      total  <n>, then one hex fid per line
  GET /ng/track/<fid>
      key/value lines: title, artist, album, track, tracks, dur, kbps, ...
  GET /ng/poll?mac=..&st=..&id=..&pos=..&vol=..&qi=..&qn=..&wake=<port>&stall=<ms>
      reports playback state; the reply carries queued commands from the web
      UI ("cmd <verb> [args...]") and the library version ("lib <n>"). When a
      command is queued the server sends a UDP datagram to <port> on the
      receiver, which then polls at once instead of waiting a few seconds.

Nodes are paths such as "artists", "ar:12", "ar:12/al:40", "ge:3/ar:12/all",
"pl:2" or "q:7" (an ad-hoc queue created by the web UI). Each step narrows the
previous one, which is what gives the device its Artist > Album > Track
hierarchy.
"""

from __future__ import annotations

import logging
import random

from aiohttp import web

from . import ids
from .ids import device_bytes
from .library import sort_key as _sort

log = logging.getLogger(__name__)

QUEUE_LIMIT = 10000
# Receivers report the longest time their player loop was held up since the
# previous poll. The audio device buffers only a fraction of a second, so a
# longer hold-up is probably an audible skip.
STALL_WARN_MS = 250


class NodeError(Exception):
    pass


def _parse(node: str) -> list[tuple[str, int | None]]:
    out = []
    for seg in [s for s in node.strip("/").split("/") if s]:
        if ":" in seg:
            key, _, val = seg.partition(":")
            try:
                out.append((key, int(val)))
            except ValueError:
                raise NodeError(seg)
        else:
            out.append((seg, None))
    return out or [("root", None)]


class Menu:
    def __init__(self, title: str):
        self.title = title
        self.items: list[tuple[str, str, str, str]] = []
        self.tracks: list[dict] | None = None

    def add(self, kind: str, ref: str, label: str, detail: str = "") -> None:
        self.items.append((kind, ref, label, detail))


class NGProtocol:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lib = ctx.library

    def routes(self) -> list:
        return [
            web.get("/ng/menu/{node:.*}", self.menu),
            web.get("/ng/queue/{node:.*}", self.queue),
            web.get("/ng/track/{fid}", self.track),
            web.get("/ng/poll", self.poll),
        ]

    # -- node resolution ------------------------------------------------
    def resolve(self, node: str) -> Menu:
        lib = self.lib
        segs = _parse(node)
        head = segs[0][0]
        base = node.strip("/")

        if len(segs) == 1 and segs[0][1] is None:
            if head == "root":
                st = lib.stats()
                m = Menu("Music")
                m.add("M", "artists", "Artists")
                m.add("M", "albums", "Albums")
                m.add("M", "songs", "Songs")
                m.add("M", "genres", "Genres")
                if st["playlists"]:
                    m.add("M", "playlists", "Playlists")
                m.add("M", "recent", "Recently Added")
                m.add("S", "songs", "Shuffle Songs")
                return m
            if head == "artists":
                m = Menu("Artists")
                for a in lib.artists():
                    m.add("M", f"ar:{a['id']}", a["name"])
                return m
            if head == "albums":
                m = Menu("Albums")
                for al in lib.albums():
                    m.add("M", f"al:{al['id']}", al["title"], al["artist"])
                return m
            if head == "genres":
                m = Menu("Genres")
                for g in lib.genres():
                    m.add("M", f"ge:{g['id']}", g["name"])
                return m
            if head == "playlists":
                m = Menu("Playlists")
                for p in lib.playlists():
                    m.add("M", f"pl:{p['id']}", p["name"])
                return m
            if head == "recent":
                m = Menu("Recently Added")
                seen = []
                for t in lib.recently_added(500):
                    if t["album_id"] not in seen:
                        seen.append(t["album_id"])
                for alid in seen[:50]:
                    al = lib.album(alid)
                    if al:
                        m.add("M", f"al:{alid}", al["title"], al["artist"])
                return m
            if head == "songs":
                return self._tracks_menu("Songs", lib.tracks(), detail="artist")
            raise NodeError(node)

        ctx: dict[str, int | None] = {}
        for key, val in segs:
            if key not in ("ar", "al", "ge", "pl", "q", "all"):
                raise NodeError(node)
            ctx[key] = val

        if "q" in ctx:
            track_ids = self.ctx.receivers.queue(ctx["q"] or 0)
            if track_ids is None:
                raise NodeError(node)
            return self._tracks_menu("Queue", lib.tracks_by_ids(track_ids), detail="artist")
        if "pl" in ctx:
            p = lib.playlist(ctx["pl"])
            if not p:
                raise NodeError(node)
            return self._tracks_menu(p["name"], lib.playlist_tracks(p["id"]), detail="artist")
        if "al" in ctx:
            al = lib.album(ctx["al"])
            if not al:
                raise NodeError(node)
            tracks = lib.tracks(album_id=al["id"])
            compilation = len({t["artist"] for t in tracks}) > 1
            return self._tracks_menu(al["title"], tracks, detail="artist" if compilation else "number")
        artist = lib.artist(ctx["ar"]) if "ar" in ctx else None
        genre = lib.genre(ctx["ge"]) if "ge" in ctx else None
        if ("ar" in ctx and not artist) or ("ge" in ctx and not genre):
            raise NodeError(node)
        if "all" in ctx:
            title = artist["name"] if artist else genre["name"] if genre else "All Songs"
            tracks = lib.tracks(artist_id=artist["id"] if artist else None,
                                genre_id=genre["id"] if genre else None)
            return self._tracks_menu(title, tracks, detail="album")
        if artist:
            m = Menu(artist["name"])
            albums = lib.albums(artist_id=artist["id"], genre_id=genre["id"] if genre else None)
            m.add("M", f"{base}/all", "All Songs")
            for al in albums:
                m.add("M", f"{base}/al:{al['id']}", al["title"], str(al["year"] or ""))
            return m
        if genre:
            m = Menu(genre["name"])
            m.add("M", f"{base}/all", "All Songs")
            for a in lib.genre_artists(genre["id"]):
                m.add("M", f"{base}/ar:{a['id']}", a["name"])
            return m
        raise NodeError(node)

    @staticmethod
    def _tracks_menu(title: str, tracks: list[dict], detail: str) -> Menu:
        m = Menu(title)
        m.tracks = tracks
        for t in tracks:
            if detail == "number":
                d = str(t["track_no"] or "")
            elif detail == "album":
                d = t["album"]
            else:
                d = t["artist"]
            m.add("T", f"{ids.track_fid(t['id']):x}", t["title"], d)
        return m

    def tracks_for(self, node: str) -> list[dict]:
        m = self.resolve(node)
        if m.tracks is not None:
            return m.tracks
        # A menu of menus: play everything beneath it, in browsing order.
        top = node.strip("/")
        if top in ("root", "artists", "genres"):
            return sorted(self.lib.tracks(), key=_artist_order)
        if top == "albums":
            return sorted(self.lib.tracks(), key=lambda t: (_sort(t["album"]), _sort(t["album_artist"]),
                                                            t["disc_no"] or 1, t["track_no"] or 9999))
        if top == "recent":
            return self.lib.recently_added(500)
        try:
            return self.resolve(top + "/all").tracks or []
        except NodeError:
            return []

    # -- handlers -------------------------------------------------------
    def _reply(self, lines: list[str]) -> web.Response:
        body = "RNG 1\n" + "".join(line + "\n" for line in lines)
        return web.Response(body=device_bytes(body, keep_layout=True), content_type="text/plain", charset="iso-8859-1")

    def _seen(self, req: web.Request) -> None:
        if req.remote:
            self.ctx.receivers.touch(req.remote, firmware="ng", stage="running",
                                     mac=req.query.get("mac"))

    async def menu(self, req: web.Request) -> web.Response:
        self._seen(req)
        node = req.match_info["node"] or "root"
        try:
            m = self.resolve(node)
        except NodeError:
            raise web.HTTPNotFound(text="no such node")
        total = len(m.items)
        try:
            begin = max(0, int(req.query.get("b", 0)))
            count = max(0, int(req.query.get("n", total)))
        except ValueError:
            raise web.HTTPBadRequest()
        lines = [f"title\t{_clean(m.title)}", f"total\t{total}", f"begin\t{begin}"]
        for kind, ref, label, detail in m.items[begin:begin + count]:
            lines.append(f"i\t{kind}\t{ref}\t{_clean(label)}\t{_clean(detail)}")
        return self._reply(lines)

    async def queue(self, req: web.Request) -> web.Response:
        self._seen(req)
        node = req.match_info["node"] or "songs"
        try:
            tracks = self.tracks_for(node)
        except NodeError:
            raise web.HTTPNotFound(text="no such node")
        order = [t["id"] for t in tracks]
        try:
            first = int(req.query.get("first", -1))
        except ValueError:
            first = -1
        if req.query.get("shuffle") == "1":
            head = []
            if 0 <= first < len(order):
                head = [order.pop(first)]
            random.shuffle(order)
            order = head + order
        elif 0 <= first < len(order):
            order = order[first:] + order[:first] if req.query.get("wrap") == "1" else order[first:]
        order = order[:QUEUE_LIMIT]
        return self._reply([f"total\t{len(order)}"] + [f"{ids.track_fid(i):x}" for i in order])

    async def track(self, req: web.Request) -> web.Response:
        self._seen(req)
        fid = ids.parse_hex(req.match_info["fid"])
        kind = ids.decode_fid(fid) if fid is not None else None
        if not kind or kind[0] != "track":
            raise web.HTTPNotFound()
        t = self.lib.track(kind[1])
        if not t:
            raise web.HTTPNotFound()
        album_tracks = t["track_total"] or len(self.lib.tracks(album_id=t["album_id"]))
        lines = [
            f"id\t{fid:x}",
            f"title\t{_clean(t['title'])}",
            f"artist\t{_clean(t['artist'])}",
            f"album\t{_clean(t['album'])}",
            f"year\t{t['year'] or t['album_year'] or ''}",
            f"genre\t{_clean(t['genre'])}",
            f"track\t{t['track_no'] or 0}",
            f"tracks\t{album_tracks}",
            f"dur\t{t['duration_ms']}",
            f"kbps\t{t['bitrate']}",
            f"vbr\t{t['vbr']}",
            f"rate\t{t['sample_rate']}",
            f"size\t{max(0, t['size'] - t['data_offset'])}",
            "codec\tmp3",
        ]
        return self._reply(lines)

    async def poll(self, req: web.Request) -> web.Response:
        if not req.remote:
            raise web.HTTPBadRequest()
        q = req.query
        r = self.ctx.receivers.touch(req.remote, firmware="ng", stage="running", mac=q.get("mac"))
        try:
            r.wake_port = max(0, min(65535, int(q.get("wake", 0))))
        except ValueError:
            r.wake_port = 0
        state = {k: q.get(k) for k in ("st", "id", "pos", "vol", "qi", "qn", "shuf", "rep", "ver")}
        stall = q.get("stall", "")
        if stall.isdigit() and int(stall) >= STALL_WARN_MS:
            log.warning("%s: playback was held up for %s ms; audio may have skipped", r.name, stall)
        fid = ids.parse_hex(state.get("id") or "")
        kind = ids.decode_fid(fid) if fid else None
        if kind and kind[0] == "track":
            t = self.lib.track(kind[1])
            if t:
                state["track"] = {
                    "id": t["id"], "title": t["title"], "artist": t["artist"], "album": t["album"],
                    "album_id": t["album_id"], "duration_ms": t["duration_ms"],
                }
        lines = [f"lib\t{self.lib.version}"]
        while r.commands:
            cmd = r.commands.popleft()
            if cmd[0] == "vol" and len(cmd) > 1:
                # This report predates the command; show where it's going.
                state["vol"] = str(cmd[1])
            lines.append("cmd\t" + "\t".join(str(x) for x in cmd))
        r.state = state
        return self._reply(lines)


def _clean(s) -> str:
    return str(s if s is not None else "").replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _artist_order(t: dict) -> tuple:
    return (_sort(t["album_artist"]), t["album_year"] or 9999, _sort(t["album"]),
            t["disc_no"] or 1, t["track_no"] or 9999)
