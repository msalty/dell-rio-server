"""Play queues, playlists made in the web interface, play history, moving
playback between players, podcasts and alarms."""

import asyncio
import datetime as dt
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from rioserver import ids
from rioserver.alarms import AlarmClock, next_fire
from rioserver.app import Context, build_app
from rioserver.config import Config
from rioserver.history import PlayTracker
from rioserver.playqueue import PlayQueue
from rioserver.podcasts import Podcasts, parse_duration, parse_feed, parse_opml
from rioserver.receivers import ReceiverRegistry

MAC = "00:90:4b:00:00:01"


@pytest.fixture()
async def client(library, tmp_path):
    ctx = Context(Config(data_dir=tmp_path), library, ReceiverRegistry())
    ctx.tracker = PlayTracker(library)
    ctx.receivers.start_position = ctx.tracker.start_position
    async with TestClient(TestServer(build_app(ctx))) as c:
        c.ctx = ctx
        yield c


def ng_lines(text: str) -> list[list[str]]:
    lines = text.split("\n")
    assert lines[0] == "RNG 1"
    return [line.split("\t") for line in lines[1:] if line]


def fid(track_id: int) -> str:
    return f"{ids.track_fid(track_id):x}"


async def poll(client, **state) -> list[list[str]]:
    """A status poll from a receiver running firmware 1.6."""
    q = {"mac": MAC, "st": "play", "pos": "0", "vol": "50", "qi": "0", "qn": "0", "shuf": "0", "ver": "1.6"}
    q.update({k: str(v) for k, v in state.items()})
    return [r for r in ng_lines(await (await client.get("/ng/poll", params=q)).text()) if r[0] == "cmd"]


async def fetch_queue(client, node: str, **params) -> list[int]:
    rows = ng_lines(await (await client.get(f"/ng/queue/{node}", params=params)).text())
    return [ids.decode_fid(int(r[0], 16))[1] for r in rows if r[0] != "total"]


# ---- the play queue model ----------------------------------------------------

def test_play_queue_edits_keep_the_current_track():
    pq = PlayQueue()
    pq.load([{"id": i} for i in (1, 2, 3, 4)], index=1)
    pq.insert_next([{"id": 9}])
    assert pq.ids() == [1, 2, 9, 3, 4] and pq.current["id"] == 2
    pq.append([{"id": 8}])
    pq.move(4, 0)  # 4 to the front: the current track shifts along
    assert pq.ids() == [4, 1, 2, 9, 3, 8] and pq.current["id"] == 2
    pq.remove(0)
    assert pq.ids() == [1, 2, 9, 3, 8] and pq.index == 1
    with pytest.raises(ValueError):
        pq.remove(1)  # the track that's playing
    pq.set_shuffle(True)
    assert pq.ids()[:2] == [1, 2] and sorted(pq.ids()) == [1, 2, 3, 8, 9]
    pq.set_shuffle(False)
    assert pq.ids() == [1, 2, 9, 3, 8] and pq.current["id"] == 2
    pq.clear_upcoming()
    assert pq.ids() == [1, 2]


def test_play_queue_tells_duplicates_apart():
    pq = PlayQueue()
    pq.load([{"id": 5}, {"id": 5}, {"id": 6}], index=0)
    pq.set_shuffle(True)
    pq.remove(2 if pq.items[2]["id"] == 5 else 1)  # one of the two 5s (not the current one)
    pq.set_shuffle(False)
    assert pq.ids() == [5, 6] and pq.index == 0


# ---- Rio receivers: the server mirrors and edits their queue ----------------

async def test_receiver_queue_is_mirrored_and_edited(client, library):
    await poll(client, st="stop")
    abbey = library.albums_by_title("Abbey Road")[0]
    tracks = library.tracks(album_id=abbey["id"])
    # The receiver plays the album from its own menus, starting at track 1.
    order = await fetch_queue(client, f"al:{abbey['id']}", first=0, shuffle=0)
    assert order == [t["id"] for t in tracks]
    await poll(client, id=fid(tracks[0]["id"]), qi=0, qn=3)
    q = await (await client.get(f"/api/receivers/{MAC}/queue")).json()
    assert q["known"] and q["editable"] and q["index"] == 0
    assert [t["title"] for t in q["tracks"]] == ["Come Together", "Something", "Maxwell's Silver Hammer"]

    # "Play next" goes in after the current track and is sent to the receiver.
    taxman = library.tracks(album_id=library.albums_by_title("Revolver")[0]["id"])[0]
    r = await client.post(f"/api/receivers/{MAC}/queue", json={"op": "next", "track_ids": [taxman["id"]]})
    assert r.status == 200
    q = await r.json()
    assert [t["title"] for t in q["tracks"]] == ["Come Together", "Taxman", "Something", "Maxwell's Silver Hammer"]
    cmds = await poll(client, id=fid(tracks[0]["id"]), qi=0, qn=3)
    assert len(cmds) == 1 and cmds[0][:2] == ["cmd", "queue"] and cmds[0][3:] == ["0", "0", "0"]
    node = cmds[0][2]
    # The receiver fetches the new list and keeps playing track 1.
    assert await fetch_queue(client, node) == [t["id"] for t in q["tracks"]]
    await poll(client, id=fid(tracks[0]["id"]), qi=0, qn=4)
    # It moves on to Taxman: the mirror follows.
    await poll(client, id=fid(taxman["id"]), qi=1, qn=4)
    q = await (await client.get(f"/api/receivers/{MAC}/queue")).json()
    assert q["index"] == 1 and q["known"]

    # Reorder and jump.
    r = await client.post(f"/api/receivers/{MAC}/queue", json={"op": "move", "pos": 3, "to": 2})
    assert [t["title"] for t in (await r.json())["tracks"]][2] == "Maxwell's Silver Hammer"
    r = await client.post(f"/api/receivers/{MAC}/queue", json={"op": "jump", "pos": 3})
    cmds = await poll(client, id=fid(taxman["id"]), qi=1, qn=4)
    assert [c[1] for c in cmds] == ["queue", "queue"] and cmds[-1][3:5] == ["3", "1"]


async def test_receiver_queue_follows_shuffle_on_the_receiver(client, library):
    await poll(client, st="stop")
    beatles = library.artist_by_name("The Beatles")
    order = await fetch_queue(client, f"ar:{beatles['id']}/all", first=0)
    await poll(client, id=fid(order[0]), qi=0, qn=len(order))
    # Random pressed on the receiver: the server sends an order it knows.
    cmds = await poll(client, id=fid(order[0]), qi=0, qn=len(order), shuf=1)
    assert cmds and cmds[0][1] == "queue" and cmds[0][3:] == ["0", "0", "1"]
    shuffled = await fetch_queue(client, cmds[0][2])
    assert shuffled[0] == order[0] and sorted(shuffled) == sorted(order)
    # and back: the album order again, at the same track
    await poll(client, id=fid(order[0]), qi=0, qn=len(order), shuf=1)
    cmds = await poll(client, id=fid(order[0]), qi=0, qn=len(order), shuf=0)
    assert await fetch_queue(client, cmds[0][2]) == order


async def test_old_firmware_queue_is_shown_but_not_edited(client, library):
    await poll(client, st="stop", ver="1.5")
    abbey = library.albums_by_title("Abbey Road")[0]
    order = await fetch_queue(client, f"al:{abbey['id']}", first=1)
    await poll(client, id=fid(order[0]), qi=0, qn=2, ver="1.5")
    q = await (await client.get(f"/api/receivers/{MAC}/queue")).json()
    assert q["known"] and not q["editable"] and len(q["tracks"]) == 2
    r = await client.post(f"/api/receivers/{MAC}/queue", json={"op": "add", "track_ids": [order[0]]})
    assert r.status == 409 and "firmware" in await r.text()


async def test_add_to_queue_on_an_idle_receiver_plays(client, library):
    await poll(client, st="stop")
    t = library.tracks()[0]
    r = await client.post(f"/api/receivers/{MAC}/queue", json={"op": "add", "track_ids": [t["id"]]})
    assert r.status == 200
    cmds = await poll(client, st="stop")
    assert cmds[0][1] == "play" and cmds[0][2].startswith("q:")


async def test_repeat_and_shuffle_commands(client, library):
    await poll(client, st="stop")
    order = await fetch_queue(client, "songs", first=0)
    await poll(client, id=fid(order[0]), qi=0, qn=len(order))
    for body in ({"cmd": "repeat", "value": "all"}, {"cmd": "shuffle", "value": "1"}):
        assert (await client.post(f"/api/receivers/{MAC}/command", json=body)).status == 200
    cmds = await poll(client, id=fid(order[0]), qi=0, qn=len(order))
    assert cmds[0] == ["cmd", "repeat", "all"]
    assert cmds[1][1] == "queue" and cmds[1][-1] == "1"


# ---- playlists ------------------------------------------------------------------

async def test_playlists_made_in_the_web_interface(client, library):
    ids_ = [t["id"] for t in library.tracks()[:3]]
    r = await client.post("/api/playlists", json={"name": "  Morning  mix ", "track_ids": ids_[:2]})
    p = await r.json()
    assert p["name"] == "Morning mix" and p["editable"]
    await client.post(f"/api/playlists/{p['id']}/add", json={"track_ids": [ids_[2], ids_[0]]})
    got = await (await client.get(f"/api/playlists/{p['id']}")).json()
    assert [t["id"] for t in got["tracks"]] == ids_ + [ids_[0]]
    r = await client.post(f"/api/playlists/{p['id']}", json={"name": "Wake up", "track_ids": ids_[::-1]})
    got = await r.json()
    assert got["name"] == "Wake up" and [t["id"] for t in got["tracks"]] == ids_[::-1]

    # It's on the receivers too, and survives a rescan (it isn't an .m3u file).
    library.scan(full=True)
    rows = ng_lines(await (await client.get("/ng/menu/playlists")).text())
    assert ["i", "M", f"pl:{p['id']}", "Wake up", ""] in rows
    lists = await (await client.get("/api/playlists")).json()
    road = next(x for x in lists if x["name"] == "Road Trip")
    assert not road["editable"]
    assert (await client.post(f"/api/playlists/{road['id']}", json={"name": "x"})).status == 409
    assert (await client.delete(f"/api/playlists/{p['id']}")).status == 200
    assert all(x["id"] != p["id"] for x in await (await client.get("/api/playlists")).json())


# ---- play history --------------------------------------------------------------

async def test_plays_are_counted_from_receiver_polls(client, library):
    tracks = library.tracks()
    a, b = tracks[0], tracks[1]
    await poll(client, st="play", id=fid(a["id"]), pos=100)
    assert library.most_played() == []  # not yet half way
    await poll(client, st="play", id=fid(a["id"]), pos=a["duration_ms"] // 2 + 10)
    await poll(client, st="play", id=fid(a["id"]), pos=a["duration_ms"] - 10)  # still one play
    await poll(client, st="play", id=fid(b["id"]), pos=b["duration_ms"] - 10)
    await poll(client, st="play", id=fid(a["id"]), pos=a["duration_ms"] - 10)
    top = await (await client.get("/api/history/top")).json()
    assert [(t["id"], t["play_count"]) for t in top] == [(a["id"], 2), (b["id"], 1)]
    recent = await (await client.get("/api/history/recent")).json()
    assert [t["id"] for t in recent] == [a["id"], b["id"]]
    log = await (await client.get("/api/history/log")).json()
    assert len(log) == 3 and log[0]["device"].startswith("Receiver")
    # ...and the receiver lists them, iPod style.
    rows = ng_lines(await (await client.get("/ng/menu/top25")).text())
    assert [r[3] for r in rows if r[0] == "i"] == [a["title"], b["title"]]
    rows = ng_lines(await (await client.get("/ng/menu/playlists")).text())
    assert [r[3] for r in rows if r[0] == "i"][:2] == ["Top 25 Most Played", "Recently Played"]


async def test_browser_plays_are_counted(client, library):
    t = library.tracks()[2]
    for pos in (0, t["duration_ms"]):
        await client.post("/api/progress", json={"track_id": t["id"], "pos_ms": pos, "st": "play", "client": "abc"})
    assert [r["id"] for r in library.most_played()] == [t["id"]]


# ---- moving playback --------------------------------------------------------------

async def test_transfer_from_receiver_to_browser_and_back(client, library):
    await poll(client, st="stop")
    abbey = library.albums_by_title("Abbey Road")[0]
    order = await fetch_queue(client, f"al:{abbey['id']}", first=0)
    await poll(client, st="play", id=fid(order[1]), qi=1, qn=3, pos=600)
    r = await client.post(f"/api/receivers/{MAC}/transfer", json={"to": ""})
    body = await r.json()
    assert body["track_ids"] == order and body["index"] == 1 and body["pos_ms"] == 600
    cmds = await poll(client, st="play", id=fid(order[1]), qi=1, qn=3)
    assert cmds == [["cmd", "pause"]]
    # The browser sends it back, starting where it was.
    r = await client.post(f"/api/receivers/{MAC}/play", json={"track_ids": order, "index": 1, "start_ms": 700})
    cmds = await poll(client, st="pause", id=fid(order[1]), qi=1, qn=3)
    assert cmds[0][1] == "play" and cmds[0][3] == "1" and cmds[1] == ["cmd", "seek", "700"]


# ---- podcasts ---------------------------------------------------------------------

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Test Talk</title>
  <itunes:author>Ann Host</itunes:author>
  <description>&lt;p&gt;A show about &lt;b&gt;tests&lt;/b&gt;.&lt;/p&gt;</description>
  <itunes:image href="{base}/cover.jpg"/>
  {items}
</channel>
</rss>"""
ITEM = """<item><title>{title}</title><guid>{guid}</guid>
  <pubDate>{date}</pubDate><itunes:duration>{dur}</itunes:duration>
  <enclosure url="{url}" type="{mime}" length="1234"/><description>Notes for {title}</description></item>"""


def make_feed(base: str, episodes: list[tuple]) -> str:
    items = []
    for n, (title, mime, path) in enumerate(episodes):
        date = (dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=n)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000")
        items.append(ITEM.format(title=title, guid=f"ep-{n}", date=date, dur="00:00:01",
                                 url=f"{base}{path}", mime=mime))
    return FEED.format(base=base, items="\n".join(items))


@pytest.fixture()
async def feed_server(music_dir):
    mp3 = next(music_dir.rglob("*.mp3")).read_bytes()
    state = {"episodes": [("Episode 1", "audio/mpeg", "/ep1.mp3"), ("Episode 2", "audio/mpeg", "/ep2.mp3"),
                          ("Episode 3", "audio/x-m4a", "/ep3.m4a"), ("Episode 4", "audio/mpeg", "/ep4.mp3")],
             "hits": 0}
    app = web.Application()

    async def feed(req):
        state["hits"] += 1
        return web.Response(text=make_feed(state["base"], state["episodes"]), content_type="application/rss+xml")

    async def episode(req):
        return web.Response(body=mp3, content_type="audio/mpeg")

    async def cover(req):
        return web.Response(body=b"\xff\xd8\xff fake jpeg", content_type="image/jpeg")

    app.router.add_get("/feed.xml", feed)
    app.router.add_get("/cover.jpg", cover)
    app.router.add_get("/{name}.mp3", episode)
    server = TestServer(app)
    await server.start_server()
    state["base"] = f"http://127.0.0.1:{server.port}"
    state["url"] = state["base"] + "/feed.xml"
    yield state
    await server.close()


async def until(predicate, timeout=10.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not (value := predicate()):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)
    return value


@pytest.fixture()
async def pod_client(library, tmp_path):
    ctx = Context(Config(data_dir=tmp_path), library, ReceiverRegistry())
    ctx.podcasts = Podcasts(library, tmp_path, refresh_interval=0)
    ctx.tracker = PlayTracker(library, ctx.podcasts)
    ctx.receivers.start_position = ctx.tracker.start_position
    async with TestClient(TestServer(build_app(ctx))) as c:
        ctx.podcasts.start()
        c.ctx = ctx
        yield c
        await ctx.podcasts.close()


def test_feed_parsing():
    show = parse_feed(make_feed("http://x", [("A", "audio/mpeg", "/a.mp3"), ("B", "audio/mpeg", "/b.mp3")]).encode())
    assert show["title"] == "Test Talk" and show["author"] == "Ann Host"
    assert show["description"] == "A show about tests." and show["image_url"] == "http://x/cover.jpg"
    assert [e["title"] for e in show["episodes"]] == ["B", "A"]  # newest first
    assert show["episodes"][0]["duration_ms"] == 1000
    assert parse_duration("1:02:03") == 3723000 and parse_duration("90") == 90000
    opml = b'<opml><body><outline text="x" type="rss" xmlUrl="http://a/feed"/><outline xmlUrl="http://b"/></body></opml>'
    assert parse_opml(opml) == ["http://a/feed", "http://b"]


async def test_podcast_subscribe_download_and_resume(pod_client, feed_server, library):
    c = pod_client
    r = await c.post("/api/podcasts", json={"url": feed_server["url"], "keep": 2})
    assert r.status == 200, await r.text()
    show = await r.json()
    assert show["title"] == "Test Talk" and len(show["episodes"]) == 4
    by_title = {e["title"]: e for e in show["episodes"]}
    assert by_title["Episode 3"]["status"] == "unsupported"  # not an MP3
    # The newest two MP3s download by themselves.
    pid = show["id"]

    def ready():
        eps = c.ctx.podcasts.episodes(pid)
        return sorted(e["title"] for e in eps if e["status"] == "ready")
    await until(lambda: ready() == ["Episode 2", "Episode 4"], what="downloads")
    assert c.ctx.podcasts.cover_path(pid) is not None
    assert (await c.get(f"/api/art/podcast/{pid}")).status == 200

    # Episodes stay out of the music library...
    assert all(a["name"] != "Test Talk" for a in library.artists())
    assert library.stats()["tracks"] == 10
    # ...but the receiver has a Podcasts menu, newest first.
    root = ng_lines(await (await c.get("/ng/menu/root")).text())
    assert ["i", "M", "podcasts", "Podcasts", ""] in root
    rows = ng_lines(await (await c.get("/ng/menu/podcasts")).text())
    assert rows[-1][1:4] == ["M", f"pc:{pid}", "Test Talk"]
    rows = [r for r in ng_lines(await (await c.get(f"/ng/menu/pc:{pid}")).text()) if r[0] == "i"]
    assert [(r[3], r[4]) for r in rows] == [("Episode 4", "New"), ("Episode 2", "New")]
    ep4 = ids.decode_fid(int(rows[0][2], 16))[1]
    # Picking an episode plays just that episode.
    assert await fetch_queue(c, f"pc:{pid}", first=0) == [ep4]

    # Listened to half way in the browser...
    t = library.track(ep4)
    half = t["duration_ms"] // 2
    await c.post("/api/progress", json={"track_id": ep4, "pos_ms": half, "st": "pause", "client": "phone"})
    # ...a receiver starting it carries on from there (a little before).
    await poll(c, st="stop")
    info = dict(r for r in ng_lines(await (await c.get(f"/ng/track/{fid(ep4)}")).text()) if len(r) == 2)
    assert 0 < int(info["start"]) <= half
    # The browser restarting it on the same device starts over...
    start = await (await c.get(f"/api/tracks/{ep4}/start", params={"client": "phone"})).json()
    assert start["start_ms"] > 0  # it was paused, so this is resuming
    # ...and once it has been played to the end, it starts from the top.
    await poll(c, st="play", id=fid(ep4), pos=t["duration_ms"])
    show = await (await c.get(f"/api/podcasts/{pid}")).json()
    ep = next(e for e in show["episodes"] if e["track_id"] == ep4)
    assert ep["played"] == 1
    info = dict(r for r in ng_lines(await (await c.get(f"/ng/track/{fid(ep4)}")).text()) if len(r) == 2)
    assert "start" not in info

    # Download an older one by hand; delete; unsubscribe.
    ep1 = by_title["Episode 1"]
    r = await c.post(f"/api/episodes/{ep1['id']}/download")
    await until(lambda: c.ctx.podcasts.episode(ep1["id"])["status"] == "ready", what="manual download")
    assert c.ctx.podcasts.episode(ep1["id"])["pinned"] == 1
    r = await c.delete(f"/api/episodes/{ep1['id']}/download")
    assert (await r.json())["status"] == "new"
    opml = await (await c.get("/api/podcasts/opml")).text()
    assert feed_server["url"] in opml
    assert (await c.delete(f"/api/podcasts/{pid}")).status == 200
    assert await (await c.get("/api/podcasts")).json() == []
    assert not any(t["podcast_id"] for t in library.tracks_by_ids(list(range(1, 50))))


async def test_bad_feed_is_refused(pod_client, feed_server):
    r = await pod_client.post("/api/podcasts", json={"url": feed_server["base"] + "/cover.jpg"})
    assert r.status == 400 and "feed" in await r.text()
    r = await pod_client.post("/api/podcasts", json={"url": "ftp://nope"})
    assert r.status == 400


# ---- alarms -------------------------------------------------------------------------

def test_next_alarm_time():
    tz = "Europe/Amsterdam"
    base = dt.datetime(2026, 11, 2, 6, 0, tzinfo=dt.timezone(dt.timedelta(hours=1))).timestamp()  # a Monday
    a = {"enabled": True, "time": "07:00", "days": [], "tz": tz}
    assert dt.datetime.fromtimestamp(next_fire(a, base), dt.timezone.utc).hour == 6  # 07:00 CET
    weekend = {**a, "days": [5, 6]}
    assert dt.datetime.fromtimestamp(next_fire(weekend, base), dt.timezone.utc).weekday() == 5
    assert next_fire({**a, "enabled": False}, base) is None


async def test_alarm_goes_off_and_fades_in(client, library, monkeypatch):
    from rioserver import web_api

    monkeypatch.setattr(web_api, "FADE_STEPS", 3)
    await poll(client, st="standby")
    fired = []

    async def fire(alarm):
        fired.append(alarm["id"])
        await client.ctx.web.fire_alarm({**alarm, "fade": 0.01})
    clock = AlarmClock(client.ctx.config.data_dir / "alarms.json", fire)
    client.ctx.alarms = clock
    abbey = library.albums_by_title("Abbey Road")[0]
    r = await client.post("/api/alarms", json={"time": "07:30", "days": [], "receiver": MAC, "tz": "UTC",
                                               "source": {"type": "album", "id": abbey["id"]}, "volume": 30})
    alarms = await r.json()
    assert len(alarms) == 1 and alarms[0]["next"] is not None
    when = alarms[0]["next"]
    clock._last_check = when - 30
    await clock.check(now=when + 1)
    assert fired == [alarms[0]["id"]]
    assert not clock.alarms[0]["enabled"]  # a one-off alarm switches itself off
    # Silence first, then the music...
    cmds = await poll(client, st="standby")
    assert cmds[0] == ["cmd", "vol", "1"] and cmds[1][1] == "play"
    # ...fading up to the alarm's volume.
    await asyncio.sleep(1.0)
    cmds = await poll(client, st="play")
    assert cmds[-1] == ["cmd", "vol", "30"]
    # A saved alarm survives a restart.
    again = AlarmClock(client.ctx.config.data_dir / "alarms.json", fire)
    assert again.alarms[0]["time"] == "07:30"
    assert (await client.post("/api/alarms", json={"time": "25:00", "receiver": MAC})).status == 400
