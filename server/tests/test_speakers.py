"""Receivers as AirPlay and Spotify Connect speakers, with fake shairport-sync
and librespot processes (fake_speaker.py) standing in for the real ones."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rioserver import ids, live, speakers
from rioserver.app import Context, build_app
from rioserver.config import Config
from rioserver.history import PlayTracker
from rioserver.receivers import ReceiverRegistry
from rioserver.speakers import Speakers, airplay_volume, parse_items

pytestmark = pytest.mark.skipif(not live.find_encoder(), reason="needs lame or ffmpeg")

MAC = "00:90:4b:00:00:08"
SID = "00904b000008"
FAKE = Path(__file__).with_name("fake_speaker.py")


class FakeZeroconf:
    def __init__(self):
        self.registered = {}
        self.history = []

    def get(self):
        return self

    @property
    def started(self):
        return True

    def register_service(self, info, ttl=None, allow_name_change=False):
        self.registered[info.name] = info
        self.history.append(("register", info.name))

    def unregister_service(self, info):
        self.registered.pop(info.name, None)
        self.history.append(("unregister", info.name))


@pytest.fixture()
async def client(library, tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "RECONCILE_SECONDS", 0.2)
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    monkeypatch.setenv("FAKE_SPEAKER_DIR", str(fakes))
    bins = {}
    for kind in ("shairport", "librespot"):
        p = tmp_path / f"fake-{kind}"
        p.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' {kind} \"$@\"\n")
        p.chmod(0o755)
        bins[kind] = str(p)
    cfg = Config(data_dir=tmp_path, server_ip="127.0.0.1", shairport_bin=bins["shairport"],
                 librespot_bin=bins["librespot"])
    ctx = Context(cfg, library, ReceiverRegistry())
    ctx.tracker = PlayTracker(library)
    zc = FakeZeroconf()
    ctx.speakers = Speakers(cfg, ctx.receivers, zc)
    await ctx.speakers.start()
    async with TestClient(TestServer(build_app(ctx))) as c:
        c.ctx, c.zc, c.fakes = ctx, zc, fakes
        yield c
    await ctx.speakers.close()


def ng_lines(text: str) -> list[list[str]]:
    lines = text.split("\n")
    assert lines[0] == "RNG 1"
    return [line.split("\t") for line in lines[1:] if line]


async def poll(client, **state) -> list[list[str]]:
    """A status poll from a receiver running firmware 1.8; returns its commands."""
    q = {"mac": MAC, "st": "stop", "pos": "0", "vol": "50", "qi": "0", "qn": "0", "shuf": "0", "ver": "1.8",
         "live": "0", "id": "0"}
    q.update({k: str(v) for k, v in state.items()})
    return [r[1:] for r in ng_lines(await (await client.get("/ng/poll", params=q)).text()) if r[0] == "cmd"]


async def wait_for(fn, timeout=8.0, step=0.1):
    deadline = time.monotonic() + timeout
    while True:
        result = await fn() if asyncio.iscoroutinefunction(fn) else fn()
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(step)


async def poll_until(client, verb, timeout=8.0, **state) -> list[list[str]]:
    """Poll (as a receiver does) until a command `verb` arrives; returns all commands seen."""
    seen = []

    async def check():
        seen.extend(await poll(client, **state))
        return any(c[0] == verb for c in seen)
    await wait_for(check, timeout, 0.2)
    return seen


def starts(client, instance: str) -> list[list[str]]:
    log = client.fakes / f"{instance}.log"
    if not log.exists():
        return []
    return [json.loads(line[6:]) for line in log.read_text().splitlines() if line.startswith("start ")]


async def control(client, instance: str, **cmd) -> None:
    ctl = client.fakes / f"{instance}.ctl"
    await wait_for(ctl.exists)
    fd = os.open(ctl, os.O_WRONLY | os.O_NONBLOCK)
    os.write(fd, (json.dumps(cmd) + "\n").encode())
    os.close(fd)


async def api(client, path, body=None):
    if body is None:
        resp = await client.get(path)
    else:
        resp = await client.post(path, json=body)
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def receiver(client) -> dict:
    return next(r for r in await api(client, "/api/receivers") if r["id"] == MAC)


async def switch_on(client, **switches):
    await poll(client)
    await api(client, f"/api/receivers/{MAC}/speakers", switches)
    for kind in switches:
        await wait_for(lambda kind=kind: (client.fakes / f"{kind}-0.ctl").exists())


async def play_album(client, library) -> int:
    """Play an album from the web interface and have the receiver take it, so
    its queue is known (what a live session saves)."""
    album = library.albums()[0]["id"]
    await api(client, f"/api/receivers/{MAC}/play", {"album_id": album})
    cmds = await poll(client)
    play = next(c for c in cmds if c[0] == "play")
    ng_lines(await (await client.get(f"/ng/queue/{play[1]}", params={"mac": MAC, "first": play[2]})).text())
    first = library.tracks(album_id=album)[0]["id"]
    playing = {"st": "play", "id": f"{ids.track_fid(first):x}", "qi": 0,
               "qn": len(library.tracks(album_id=album)), "pos": 12000}
    await poll(client, **playing)
    return first, playing


async def start_airplay(client, state=None, **meta):
    meta = {"title": "Song", "artist": "Artist", "album": "Album", "sender": "Anna's iPhone", **meta}
    await control(client, "airplay-0", play=60, **meta)
    return await poll_until(client, "live", **(state or {}))


# ---- pure helpers -----------------------------------------------------------

def test_shairport_metadata_items_are_parsed():
    def item(t, c, d=b""):
        import base64
        x = f"<item><type>{t.encode().hex()}</type><code>{c.encode().hex()}</code><length>{len(d)}</length>"
        if d:
            x += f'\n<data encoding="base64">\n{base64.b64encode(d).decode()}</data>'
        return (x + "</item>\n").encode()
    buf = item("core", "minm", "Héllo".encode()) + item("ssnc", "pbeg") + item("ssnc", "PICT", b"\x89PNG..")[:30]
    items, rest = parse_items(buf)
    assert items == [("core", "minm", "Héllo".encode()), ("ssnc", "pbeg", b"")]
    items, rest = parse_items(rest + item("ssnc", "PICT", b"\x89PNG..")[30:])
    assert items == [("ssnc", "PICT", b"\x89PNG..")] and rest == b"\n"


def test_airplay_volume_maps_to_the_receiver_scale():
    assert airplay_volume("0.00,0,0,0") == 100
    assert airplay_volume("-15.00,-20.00,-96.30,0.00") == 50
    assert airplay_volume("-30.00,0,0,0") == 0
    assert airplay_volume("-144.00,0,0,0") == 0  # mute
    assert airplay_volume("nonsense") is None


# ---- the processes ------------------------------------------------------------

async def test_switches_start_speakers_named_after_the_receiver(client):
    await poll(client)
    await api(client, f"/api/receivers/{MAC}/name", {"name": "Kitchen"})
    r = await receiver(client)
    assert r["speakers"] == {"airplay": False, "spotify": False, "needs_update": False,
                             "available": {"airplay": True, "spotify": True}}
    assert not list(client.fakes.iterdir())  # off by default: nothing runs
    await switch_on(client, airplay=True, spotify=True)

    # librespot answers mDNS itself, only for the LAN address, on a fixed port
    lib_args = starts(client, "spotify-0")[0]
    assert lib_args[lib_args.index("--name") + 1] == "Kitchen"
    assert lib_args[lib_args.index("--zeroconf-interface") + 1] == "127.0.0.1"
    assert lib_args[lib_args.index("--zeroconf-port") + 1] == "5300"
    assert lib_args[lib_args.index("--backend") + 1] == "pipe"

    # shairport-sync's advertisement goes out from the server, under its own host name
    await wait_for(lambda: client.zc.registered)
    info = client.zc.registered["ABCDEF012345@Kitchen._raop._tcp.local."]
    assert info.server == "rioserver.local." and info.port == 5100
    assert info.parsed_addresses() == ["127.0.0.1"]
    assert info.properties[b"am"] == b"ShairportSync"
    conf = (client.ctx.speakers.run_dir / "airplay-0.conf").read_text()
    assert 'name = "Kitchen";' in conf and "udp_port_base = 5101;" in conf

    # renaming the receiver restarts its speakers with the new name
    await api(client, f"/api/receivers/{MAC}/name", {"name": "Den"})
    await wait_for(lambda: len(starts(client, "spotify-0")) == 2 and "ABCDEF012345@Den._raop._tcp.local." in client.zc.registered)
    assert "ABCDEF012345@Kitchen._raop._tcp.local." not in client.zc.registered
    assert starts(client, "spotify-0")[1][starts(client, "spotify-0")[1].index("--name") + 1] == "Den"

    # switched off: the process stops and the advertisement is withdrawn
    await api(client, f"/api/receivers/{MAC}/speakers", {"airplay": False})
    await wait_for(lambda: not client.zc.registered)
    assert (await receiver(client))["speakers"]["airplay"] is False
    settings = json.loads((client.ctx.config.data_dir / "speakers.json").read_text())
    assert settings[MAC] == {"airplay": False, "spotify": True, "slot": 0}


async def test_firmware_before_1_8_gets_no_speakers(client):
    await poll(client, ver="1.7")
    await api(client, f"/api/receivers/{MAC}/speakers", {"airplay": True})
    await asyncio.sleep(0.8)
    assert not (client.fakes / "airplay-0.log").exists()
    assert (await receiver(client))["speakers"]["needs_update"] is True


# ---- sessions -------------------------------------------------------------------

async def test_an_airplay_session_plays_on_the_receiver(client, library):
    await switch_on(client, airplay=True)
    first, playing = await play_album(client, library)
    plays = library.recently_played(10)
    cmds = await start_airplay(client, playing)
    live_cmd = next(c for c in cmds if c[0] == "live")
    assert live_cmd[1] == f"/live/{SID}"
    assert live_cmd[2].startswith("AirPlay")
    if not any(c[0] == "meta" and c[2] == "Song" for c in cmds):
        cmds += await poll_until(client, "meta")
    meta = [c for c in cmds if c[0] == "meta"][-1]
    assert meta[1:] == ["AirPlay · Anna's iPhone", "Song", "Artist", "Album"]
    assert ["vol", "50"] in cmds  # the phone's volume (-15 of -30..0)

    # the receiver plays the MP3 stream
    resp = await client.get(f"/live/{SID}")
    assert resp.status == 200 and resp.headers["Content-Type"] == "audio/mpeg"
    data = await asyncio.wait_for(resp.content.readexactly(20000), 5)
    assert data[0] == 0xFF
    resp.close()
    await poll(client, st="play", live=1)

    # the web interface shows it like a Chromecast taken over by another app
    r = await receiver(client)
    assert r["state"]["app"] == "AirPlay" and "track" not in r["state"]
    assert r["state"]["live"]["title"] == "Song" and r["state"]["live"]["sender"] == "Anna's iPhone"
    art = await client.get(r["state"]["live"]["art"])
    assert art.status == 200 and (await art.read()).startswith(b"\xff\xd8")
    assert not r["resumable"]

    # the phone stops: the receiver stops, and its old queue can be resumed
    await control(client, "airplay-0", stop=True)
    await poll_until(client, "stop", live=1, st="play")
    r = await receiver(client)
    assert "live" not in r["state"] and r["resumable"]
    await api(client, f"/api/receivers/{MAC}/resume", {})
    cmds = await poll(client)
    play = next(c for c in cmds if c[0] == "play")
    assert ["seek", "12000"] in cmds
    queue = ng_lines(await (await client.get(f"/ng/queue/{play[1]}")).text())
    assert ids.decode_fid(int(queue[1][0], 16))[1] == first
    assert not (await receiver(client))["resumable"]
    # live audio doesn't count as a play
    assert library.recently_played(10) == plays


async def test_the_newest_sender_wins(client, library):
    await switch_on(client, airplay=True, spotify=True)
    await start_airplay(client)
    await poll(client, st="play", live=1)
    await control(client, "spotify-0", play=60, title="Other Song", artist="Band", album="LP", sender="Pixel")
    cmds = await poll_until(client, "meta", live=1, st="play")
    assert not [c for c in cmds if c[0] == "live"]  # the stream carries on; only what's on it changes
    await wait_for(lambda: len(starts(client, "airplay-0")) == 2)  # the AirPlay sender was disconnected
    cmds += await poll(client, live=1, st="play")

    async def spotify_meta():
        cmds.extend(await poll(client, live=1, st="play"))
        return any(c[0] == "meta" and c[2] == "Other Song" for c in cmds)
    await wait_for(spotify_meta)
    assert [c for c in cmds if c[0] == "meta"][-1][1:] == ["Spotify · Pixel", "Other Song", "Band", "LP"]
    r = await receiver(client)
    assert r["state"]["app"] == "Spotify" and r["state"]["live"]["art"] == "https://i.scdn.co/image/abc"

    # Spotify's volume (0-65535) and pause
    await control(client, "spotify-0", volume=32768)
    await poll_until(client, "vol", live=1, st="play")
    await control(client, "spotify-0", pause=True)
    await wait_for(lambda: client.ctx.speakers.sessions[MAC].paused_at is not None)
    assert (await receiver(client))["state"]["st"] == "pause"
    await control(client, "spotify-0", stop=True)
    await poll_until(client, "stop", live=1, st="play")


async def test_music_started_on_the_receiver_ends_the_session(client, library):
    await switch_on(client, airplay=True)
    await start_airplay(client)
    await poll(client, st="play", live=1)
    # Someone picks an album in the receiver's own menus.
    fid = f"{ids.track_fid(library.tracks()[0]['id']):x}"
    cmds = await poll(client, st="play", live=0, id=fid, qn=3)
    assert not [c for c in cmds if c[0] == "stop"]  # it is playing that now
    assert MAC not in client.ctx.speakers.sessions
    await wait_for(lambda: len(starts(client, "airplay-0")) == 2)  # the phone was disconnected


async def test_music_started_from_the_web_ends_the_session(client, library):
    await switch_on(client, airplay=True)
    await start_airplay(client)
    await poll(client, st="play", live=1)
    album = library.albums()[0]["id"]
    await api(client, f"/api/receivers/{MAC}/play", {"album_id": album})
    cmds = await poll(client, st="play", live=1)
    assert [c[0] for c in cmds] == ["play"]
    assert MAC not in client.ctx.speakers.sessions
    await wait_for(lambda: len(starts(client, "airplay-0")) == 2)


async def test_stop_in_the_web_interface_ends_the_session(client):
    await switch_on(client, airplay=True)
    await start_airplay(client)
    await poll(client, st="play", live=1)
    await api(client, f"/api/receivers/{MAC}/command", {"cmd": "next"})  # nothing to skip to: ignored
    await api(client, f"/api/receivers/{MAC}/command", {"cmd": "vol", "value": 30})
    await api(client, f"/api/receivers/{MAC}/command", {"cmd": "toggle"})  # pause works like stop
    cmds = await poll(client, st="play", live=1)
    assert [c[0] for c in cmds] == ["vol", "stop"]
    assert MAC not in client.ctx.speakers.sessions
    resp = await client.get(f"/live/{SID}")
    assert resp.status == 404
    await wait_for(lambda: len(starts(client, "airplay-0")) == 2)


# ---- browsers used as receivers ---------------------------------------------

async def test_a_browser_receiver_is_an_airplay_speaker_too(client, library):
    """A phone or tablet in receiver mode gets its own AirPlay speaker; the
    session reaches the page as a 'live' message over its WebSocket."""
    from tests.test_browser_receiver import connect

    page = await connect(client, cid="tablet1", name="Living Room")
    pid = "web-tablet1"
    await api(client, f"/api/receivers/{pid}/name", {"name": "Living Room"})
    async def offered():
        r = next(x for x in await api(client, "/api/receivers") if x["id"] == pid)
        return r["speakers"]
    assert (await wait_for(offered))["airplay"] is False  # offered, and off to begin with
    await api(client, f"/api/receivers/{pid}/speakers", {"airplay": True})
    await wait_for(lambda: (client.fakes / "airplay-0.ctl").exists())
    await wait_for(lambda: "ABCDEF012345@Living Room._raop._tcp.local." in client.zc.registered)

    # something from the library plays first, so there's a queue to come back to
    album = library.albums()[0]["id"]
    await api(client, f"/api/receivers/{pid}/play", {"album_id": album})
    load = await page.expect("load")
    await page.send(type="status", st="play", pos=500, track_id=load["track"]["id"])

    await control(client, "airplay-0", play=60, title="Song", artist="Artist", album="Album", sender="Anna's iPhone")
    live = await page.expect("live")
    assert live["url"] == f"/live/{pid}"
    meta = await page.expect("meta")
    while meta["info"]["title"] != "Song":
        meta = await page.expect("meta")
    assert meta["info"]["label"] == "AirPlay · Anna's iPhone" and meta["info"]["art"].startswith("/api/receivers/")
    await page.send(type="status", st="play", pos=0, track_id=None, live=1)

    # the stream is a higher bitrate than a Rio's
    resp = await client.get(live["url"])
    assert resp.status == 200
    await asyncio.wait_for(resp.content.readexactly(4096), 5)
    resp.close()
    assert client.ctx.speakers.streams[pid].bitrate == 320
    r = next(x for x in await api(client, "/api/receivers") if x["id"] == pid)
    assert r["state"]["app"] == "AirPlay" and r["state"]["live"]["title"] == "Song"
    assert r["queue_saved"] and "track" not in r["state"]

    # pause from the web interface (or the page's own display) ends it
    await api(client, f"/api/receivers/{pid}/command", {"cmd": "toggle"})
    await page.expect("stop")
    assert pid not in client.ctx.speakers.sessions
    await wait_for(lambda: len(starts(client, "airplay-0")) == 2)  # the phone was disconnected
    await page.send(type="status", st="stop", pos=0, track_id=None, live=0)
    r = next(x for x in await api(client, "/api/receivers") if x["id"] == pid)
    assert r["resumable"] and "live" not in r["state"]
    await api(client, f"/api/receivers/{pid}/resume", {})
    assert (await page.expect("load"))["track"]["id"] == load["track"]["id"]


async def test_a_browser_that_gives_up_on_the_stream_ends_the_session(client):
    from tests.test_browser_receiver import connect

    page = await connect(client, cid="tablet2")
    pid = "web-tablet2"
    await api(client, f"/api/receivers/{pid}/speakers", {"spotify": True})
    await control(client, "spotify-0", play=60, title="Track", artist="Band", sender="Pixel")
    await page.expect("live")
    await page.send(type="status", st="play", pos=0, track_id=None, live=1)
    await wait_for(lambda: client.ctx.speakers.sessions[pid].confirmed)
    # the page was reloaded (or couldn't reconnect): it reports nothing live
    await page.send(type="status", st="stop", pos=0, track_id=None, live=0)
    await wait_for(lambda: pid not in client.ctx.speakers.sessions)
    await wait_for(lambda: len(starts(client, "spotify-0")) == 2)


async def test_chromecasts_are_not_offered_as_speakers(client):
    from rioserver.players import QueuePlayer

    class Speaker(QueuePlayer):
        kind = "cast"
    client.ctx.receivers.add(Speaker("cast-1", "Kitchen speaker", "10.0.0.9"))
    resp = await client.post("/api/receivers/cast-1/speakers", json={"airplay": True})
    assert resp.status == 400
