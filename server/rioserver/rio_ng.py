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
      key/value lines: title, artist, album, track, tracks, dur, kbps, ...,
      and "start <ms>" where a podcast episode should carry on from
      (firmware 1.6+; older firmware is sent a seek command instead)
  GET /ng/poll?mac=..&st=..&id=..&pos=..&vol=..&qi=..&qn=..&wake=<port>&stall=<ms>&under=<n>&underms=<ms>
                &gaps=<n>&gapms=<ms>&abuf=<ms>&cpu=<percent>&dec=<percent>&disp=<percent>&net=<percent>
      reports playback state; the reply carries queued commands from the web
      UI ("cmd <verb> [args...]") and the library version ("lib <n>"). When a
      command is queued the server sends a UDP datagram to <port> on the
      receiver, which then polls at once instead of waiting a few seconds.
      Commands: play <node> <first> <shuffle>, queue <node> <index> <start>
      <shuffle> (1.6+: replace the queue, keep playing), pause, resume,
      toggle, stop, next, prev, vol <n>, seek <ms>, repeat <off|all|one>
      (1.6+), msg <text>.

The server keeps a copy of each receiver's queue (receivers.py): the track
list it fetched with /ng/queue, followed through the qi/qn/id it reports.

Nodes are paths such as "artists", "ar:12", "ar:12/al:40", "ge:3/ar:12/all",
"pl:2", "q:7" (an ad-hoc queue created by the web UI), "pc:4" (a podcast),
"podcasts", "top25" or "played". Each step narrows the
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
from .players import track_state

log = logging.getLogger(__name__)

QUEUE_LIMIT = 10000
# Receivers report the longest time their player loop was held up since the
# previous poll. The audio device buffers only a fraction of a second, so a
# longer hold-up is probably an audible skip.
STALL_WARN_MS = 250
# CPU use while playing (NG firmware 1.3+) that leaves too little headroom:
# a few demanding frames in a row and the audio device runs dry. On a real
# receiver 97-98% meant dropouts, while ~90% with the audio device kept full
# plays cleanly. A track's first seconds run high while the device fills,
# so it takes this many polls in a row, and it is said once per track.
CPU_WARN_PERCENT = 95
CPU_WARN_POLLS = 2


def _int(q, key: str) -> int | None:
    try:
        return int(q.get(key, ""))
    except ValueError:
        return None


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

    @property
    def podcasts(self):
        return getattr(self.ctx, "podcasts", None)

    @property
    def tracker(self):
        return getattr(self.ctx, "tracker", None)

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
                m = Menu("Music")
                m.add("M", "artists", "Artists")
                m.add("M", "albums", "Albums")
                m.add("M", "songs", "Songs")
                m.add("M", "genres", "Genres")
                m.add("M", "playlists", "Playlists")
                if self.podcasts is not None and any(p["downloaded"] for p in self.podcasts.shows()):
                    m.add("M", "podcasts", "Podcasts")
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
                # iPod-style smart playlists first, then the user's own
                m = Menu("Playlists")
                m.add("M", "top25", "Top 25 Most Played")
                m.add("M", "played", "Recently Played")
                for p in lib.playlists():
                    m.add("M", f"pl:{p['id']}", p["name"])
                return m
            if head == "top25":
                return self._tracks_menu("Top 25 Most Played", lib.most_played(25), detail="artist")
            if head == "played":
                return self._tracks_menu("Recently Played", lib.recently_played(100), detail="artist")
            if head == "podcasts":
                m = Menu("Podcasts")
                for p in self.podcasts.shows() if self.podcasts is not None else []:
                    if p["downloaded"]:
                        m.add("M", f"pc:{p['id']}", p["title"], f"{p['unplayed']} new" if p["unplayed"] else "")
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
            if key not in ("ar", "al", "ge", "pl", "q", "pc", "all"):
                raise NodeError(node)
            ctx[key] = val

        if "pc" in ctx:
            show = self.podcasts.show(ctx["pc"] or 0) if self.podcasts is not None else None
            if not show:
                raise NodeError(node)
            return self._tracks_menu(show["title"], self.podcasts.episode_tracks(show["id"]), detail="episode")
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
            elif detail == "episode":
                d = episode_detail(t)
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

    def _seen(self, req: web.Request):
        if req.remote:
            return self.ctx.receivers.touch(req.remote, firmware="ng", stage="running",
                                            mac=req.query.get("mac"))
        return None

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
        r = self._seen(req)
        node = req.match_info["node"] or "songs"
        try:
            tracks = self.tracks_for(node)
        except NodeError:
            raise web.HTTPNotFound(text="no such node")
        order = [t["id"] for t in tracks]
        orig = list(order)
        try:
            first = int(req.query.get("first", -1))
        except ValueError:
            first = -1
        shuffle = req.query.get("shuffle") == "1"
        if node.strip("/").startswith("pc:") and 0 <= first < len(order):
            order = orig = [order[first]]  # an episode plays by itself, as on an iPod
        elif shuffle:
            head = []
            if 0 <= first < len(order):
                head = [order.pop(first)]
            random.shuffle(order)
            order = head + order
        elif 0 <= first < len(order):
            order = order[first:] + order[:first] if req.query.get("wrap") == "1" else order[first:]
            orig = list(order)
        order = order[:QUEUE_LIMIT]
        if r is not None:
            self.ctx.receivers.queue_fetched(r, node.strip("/"), order, orig[:QUEUE_LIMIT], shuffle)
        return self._reply([f"total\t{len(order)}"] + [f"{ids.track_fid(i):x}" for i in order])

    async def track(self, req: web.Request) -> web.Response:
        r = self._seen(req)
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
        # The receiver is starting this track: a podcast episode carries on
        # from where any device left it.
        start = self.tracker.start_position(r.key, t) if self.tracker and r is not None else 0
        if start:
            if r.queue_capable:
                lines.append(f"start\t{start}")
            else:
                self.ctx.receivers.command(r, "seek", start)
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
        t = self.lib.track(kind[1]) if kind and kind[0] == "track" else None
        if t:
            state["track"] = track_state(t)
        playing = f' during "{t["title"]}" ({t["bitrate"]} kbps)' if t else ""
        under = max(0, _int(q, "under") or 0)
        under_ms = max(0, _int(q, "underms") or 0)
        if under or under_ms:
            # The stream ran dry: data didn't arrive from here fast enough.
            # A "slow read" warning just before points at the music folder;
            # otherwise suspect the network. A wait still going on when the
            # receiver reported carries on into its next report.
            if under:
                log.warning("%s: ran out of music %s%s; waited %.1f s for data", r.name,
                            "once" if under == 1 else f"{under} times", playing, under_ms / 1000)
            else:
                log.warning("%s: waited another %.1f s for music data%s", r.name, under_ms / 1000, playing)
        _report_audio(r, q, playing, t["id"] if t else None)
        r.state = state
        # Before the commands are collected, so a queue correction this
        # report calls for goes back in this reply.
        self.ctx.receivers.queue_reported(r, state)
        if self.tracker is not None:
            pos = _int(q, "pos") or 0
            self.tracker.report(r.key, r.name, state.get("track"), state.get("st") or "", pos)
        lines = [f"lib\t{self.lib.version}"]
        while r.commands:
            cmd = r.commands.popleft()
            if cmd[0] == "vol" and len(cmd) > 1:
                # This report predates the command; show where it's going.
                state["vol"] = str(cmd[1])
            lines.append("cmd\t" + "\t".join(str(x) for x in cmd))
        return self._reply(lines)


def _report_audio(r, q, playing: str, track_id: int | None) -> None:
    """Log what NG firmware 1.3+ says about its audio output: dropouts
    (the audio device ran out of sound while playback should have been
    continuous, i.e. an audible skip), how much sound the device holds, and
    how busy the CPU was while playing."""
    abuf, cpu, stall = _int(q, "abuf"), _int(q, "cpu"), _int(q, "stall")
    if abuf and abuf != r.audio_buffer_ms:
        r.audio_buffer_ms = abuf
        log.info("%s: its audio device holds %d ms of sound", r.name, abuf)
    gaps, gap_ms = max(0, _int(q, "gaps") or 0), max(0, _int(q, "gapms") or 0)
    # where the CPU time went (firmware 1.4+): the rest is the kernel, the
    # audio and network drivers, and everything else
    shares = [f"{label} {v}%" for label, key in (("decoding", "dec"), ("display", "disp"), ("network", "net"))
              if (v := _int(q, key)) is not None and v >= 0]
    busy = f"CPU {cpu}% busy" + (f": {', '.join(shares)}" if shares else "") if cpu is not None and cpu >= 0 else ""
    context = []
    if busy:
        context.append(busy)
    if stall:
        context.append(f"longest hold-up {stall} ms")
    if r.audio_buffer_ms:
        context.append(f"audio buffer {r.audio_buffer_ms} ms")
    context = f" ({', '.join(context)})" if context else ""
    if gaps:
        log.warning("%s: audio dropped out %s, %d ms in all%s%s", r.name,
                    "once" if gaps == 1 else f"{gaps} times", gap_ms, playing, context)
    if cpu is None or cpu < 0:
        return  # not playing
    r.cpu_high_polls = r.cpu_high_polls + 1 if cpu >= CPU_WARN_PERCENT else 0
    if not gaps and r.cpu_high_polls >= CPU_WARN_POLLS and r.cpu_warned_track != track_id:
        r.cpu_warned_track = track_id
        log.warning("%s: %s while playing%s; little headroom left", r.name, busy, playing)


def episode_detail(t: dict) -> str:
    """How far into an episode the listener is, for the receiver's menu."""
    if t.get("played"):
        return "Played"
    pos, dur = t.get("position_ms") or 0, t.get("duration_ms") or 0
    if pos and dur > pos:
        return f"{max(1, round((dur - pos) / 60000))}m left"
    return "New"


def _clean(s) -> str:
    return str(s if s is not None else "").replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _artist_order(t: dict) -> tuple:
    return (_sort(t["album_artist"]), t["album_year"] or 9999, _sort(t["album"]),
            t["disc_no"] or 1, t["track_no"] or 9999)
