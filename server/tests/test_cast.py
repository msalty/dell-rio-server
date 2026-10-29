"""Chromecast support, end to end: the server connects to a fake Chromecast
over the real Cast protocol (pychromecast), and the web API drives it."""

import asyncio
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rioserver import players
from rioserver.app import Context, build_app
from rioserver.cast import CastDiscovery
from rioserver.config import Config
from rioserver.receivers import ReceiverRegistry
from tests.fake_cast import NS_RECEIVER, FakeChromecast


async def until(predicate, timeout: float = 10.0, what: str = "condition"):
    """Wait without blocking the event loop (the fake device fetches stream
    URLs from the test server, which runs on this loop)."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)


@pytest.fixture()
async def cast(library, tmp_path):
    device = FakeChromecast(tmp_path)
    ctx = Context(Config(server_ip="127.0.0.1", data_dir=tmp_path), library, ReceiverRegistry())
    async with TestClient(TestServer(build_app(ctx))) as client:
        ctx.config.http_port = client.server.port
        CastDiscovery(ctx.config, ctx.receivers, asyncio.get_running_loop())._add(device.cast_info())
        player = ctx.receivers.get(f"cast-{device.uuid}")
        await until(lambda: player.online, what="the player to connect")
        client.ctx, client.device, client.player = ctx, device, player
        try:
            yield client
        finally:
            ctx.receivers.close()
            device.close()


async def rx(client) -> dict:
    receivers = await (await client.get("/api/receivers")).json()
    return next(r for r in receivers if r["id"] == client.player.id)


def loads(device) -> list[dict]:
    return [p for p in device.received if p["type"] == "LOAD"]


async def test_cast_device_is_listed(cast):
    r = await rx(cast)
    assert r["kind"] == "cast" and r["name"] == "Kitchen speaker" and r["model"] == "Google Nest Mini"
    assert r["online"] and r["controllable"] and r["ip"] == "127.0.0.1"
    await until(lambda: cast.player.state["vol"] == "50", what="the device's volume")


async def test_play_album_streams_tracks_in_order(cast, library):
    device = cast.device
    album = next(a for a in library.albums() if a["title"] == "Abbey Road")
    tracks = library.tracks(album_id=album["id"])
    resp = await cast.post(f"/api/receivers/{cast.player.id}/play", json={"album_id": album["id"], "index": 1})
    assert resp.status == 200

    # The Default Media Receiver is launched and handed track 2 with its details...
    await until(lambda: loads(device), what="LOAD")
    assert [p["appId"] for p in device.received if p["type"] == "LAUNCH"] == ["CC1AD845"]
    media = loads(device)[0]["media"]
    base = f"http://127.0.0.1:{cast.server.port}"
    assert media["contentId"] == f"{base}/api/stream/{tracks[1]['id']}"
    assert media["contentType"] == "audio/mpeg" and media["streamType"] == "BUFFERED"
    meta = media["metadata"]
    assert meta["metadataType"] == 3 and meta["title"] == "Something" and meta["artist"] == "The Beatles"
    assert meta["albumName"] == "Abbey Road" and meta["trackNumber"] == 2
    assert meta["images"] == [{"url": f"{base}/api/art/album/{album['id']}"}]
    # ...which it can fetch from the server.
    _, status, ctype = await until(lambda: device.fetched and device.fetched[0], what="the stream fetch")
    assert status == 206 and ctype == "audio/mpeg"

    r = await until(lambda: cast.player.state.get("track"), what="playing state")
    state = (await rx(cast))["state"]
    assert state["st"] == "play" and state["track"]["title"] == "Something"
    assert state["qi"] == "1" and state["qn"] == "3"
    assert r["id"] == tracks[1]["id"]

    # At the end of the track the next one starts...
    device.finish_track()
    await until(lambda: len(loads(device)) == 2, what="the next track")
    assert loads(device)[1]["media"]["contentId"].endswith(f"/api/stream/{tracks[2]['id']}")
    await until(lambda: cast.player.state.get("track", {}).get("title") == "Maxwell's Silver Hammer")
    # ...a repeated report of the same ending doesn't skip another track...
    device._media_status(0)
    await asyncio.sleep(0.3)
    assert len(loads(device)) == 2
    # ...and after the last track the player stops.
    device.finish_track()
    await until(lambda: cast.player.state["st"] == "stop", what="the queue to end")
    await asyncio.sleep(0.3)
    assert len(loads(device)) == 2


async def test_transport_commands(cast, library):
    device, pid = cast.device, cast.player.id
    beatles = library.artist_by_name("The Beatles")
    await cast.post(f"/api/receivers/{pid}/play", json={"artist_id": beatles["id"]})
    await until(lambda: cast.player.state["st"] == "play", what="playback")

    async def command(cmd, value=None):
        body = {"cmd": cmd} if value is None else {"cmd": cmd, "value": value}
        assert (await cast.post(f"/api/receivers/{pid}/command", json=body)).status == 200

    await command("toggle")
    await until(lambda: cast.player.state["st"] == "pause", what="pause")
    assert device.commands()[-1] == "PAUSE"
    await command("toggle")
    await until(lambda: cast.player.state["st"] == "play", what="resume")
    assert device.commands()[-1] == "PLAY"

    await command("vol", 30)
    assert (await rx(cast))["state"]["vol"] == "30"  # shown at once
    await until(lambda: device.volume == 0.3, what="volume")
    assert device.commands(NS_RECEIVER)[-1] == "SET_VOLUME"

    await command("seek", 500)
    await until(lambda: device.commands()[-1] == "SEEK", what="seek")
    assert device.received[-1]["currentTime"] == 0.5

    await command("next")
    await until(lambda: cast.player.state["qi"] == "1" and len(loads(device)) == 2, what="next")
    await command("prev")  # early in the track: back to the previous one
    await until(lambda: cast.player.state["qi"] == "0" and len(loads(device)) == 3, what="prev")

    await command("stop")
    await until(lambda: device.commands()[-1] == "STOP", what="stop")
    await until(lambda: cast.player.state["st"] == "stop")
    await asyncio.sleep(0.3)
    assert len(loads(device)) == 3  # a stopped track doesn't move the queue on
    await command("resume")  # starts the current track again
    await until(lambda: len(loads(device)) == 4, what="restart")


async def test_queue_from_web_interface_and_shuffle(cast, library):
    ids = [t["id"] for t in library.tracks()][:5]
    await cast.post(f"/api/receivers/{cast.player.id}/play", json={"track_ids": ids, "index": 3, "shuffle": True})
    await until(lambda: loads(cast.device), what="LOAD")
    assert loads(cast.device)[0]["media"]["contentId"].endswith(f"/api/stream/{ids[3]}")
    assert sorted(t["id"] for t in cast.player.queue) == sorted(ids)
    assert cast.player.state["shuf"] == "1"


async def test_another_app_ends_our_queue(cast, library):
    beatles = library.artist_by_name("The Beatles")
    await cast.post(f"/api/receivers/{cast.player.id}/play", json={"artist_id": beatles["id"]})
    await until(lambda: cast.player.state["st"] == "play", what="playback")
    cast.player._launched_at = float("-inf")  # pretend our launch was a while ago
    cast.device.take_over("CC32E753", "Spotify")
    state = await until(lambda: cast.player.state.get("app") and cast.player.state, what="the takeover")
    assert state["app"] == "Spotify" and "track" not in state and state["qn"] == "0"
    # a finished report for our old track no longer advances anything
    cast.player._on_ended(beatles["id"], failed=False)
    await asyncio.sleep(0.2)
    assert len(loads(cast.device)) == 1
    # when the other app quits, the device is free again
    cast.device.take_over(None, "")
    await until(lambda: "app" not in cast.player.state, what="the other app to go")


async def test_a_track_that_fails_is_skipped_once(cast, library):
    album = next(a for a in library.albums() if a["title"] == "Jazz Classics")
    await cast.post(f"/api/receivers/{cast.player.id}/play", json={"album_id": album["id"]})
    await until(lambda: cast.player.state["st"] == "play", what="playback")
    # Devices report a failure both as LOAD_FAILED and as an ERROR status.
    first = cast.player.current["id"]
    cast.player.load_media_failed(1, 104)
    cast.player._on_ended(first, failed=True)
    await until(lambda: len(loads(cast.device)) == 2, what="the next track")
    await asyncio.sleep(0.3)
    assert len(loads(cast.device)) == 2 and cast.player.state["qi"] == "1"


async def test_volume_drags_end_on_the_last_value(cast):
    for v in (10, 20, 30, 40, 55):
        await cast.post(f"/api/receivers/{cast.player.id}/command", json={"cmd": "vol", "value": v})
    assert cast.player.state["vol"] == "55"
    await until(lambda: cast.device.volume == 0.55, what="the final volume")


async def test_cast_sleep_timer_fades_and_pauses(cast, library, monkeypatch):
    from rioserver import web_api

    monkeypatch.setattr(web_api, "SLEEP_FADE", 0.1)
    beatles = library.artist_by_name("The Beatles")
    await cast.post(f"/api/receivers/{cast.player.id}/play", json={"artist_id": beatles["id"]})
    await until(lambda: cast.player.state["st"] == "play", what="playback")
    r = await cast.post(f"/api/receivers/{cast.player.id}/sleep", json={"minutes": 0.001})
    assert (await r.json())["sleep_at"] is not None
    await until(lambda: cast.player.state["st"] == "pause", what="the sleep timer")
    await until(lambda: cast.device.volume == 0.5, what="the volume to come back")


async def test_cast_players_can_be_renamed(cast):
    r = await cast.post(f"/api/receivers/{cast.player.id}/name", json={"name": "Den"})
    assert (await r.json())["name"] == "Den"
    assert (await rx(cast))["name"] == "Den"


def test_play_order():
    assert players.play_order(4, 2, False) == ([0, 1, 2, 3], 2)
    order, start = players.play_order(6, 4, True)
    assert start == 0 and order[0] == 4 and sorted(order) == list(range(6))


async def test_devices_are_found_over_mdns(tmp_path):
    import socket

    import zeroconf

    device = FakeChromecast(tmp_path, name="Whole house")
    # Advertise it the way a speaker group does (groups use a port other than 8009).
    zc = await asyncio.to_thread(zeroconf.Zeroconf)
    service = zeroconf.ServiceInfo(
        "_googlecast._tcp.local.", f"Group-{device.uuid.hex}._googlecast._tcp.local.",
        addresses=[socket.inet_aton("127.0.0.1")], port=device.port, server="fake-group.local.",
        properties={"id": device.uuid.hex, "md": "Google Cast Group", "fn": "Whole house"})
    await asyncio.to_thread(zc.register_service, service)
    registry = ReceiverRegistry()
    discovery = CastDiscovery(Config(server_ip="127.0.0.1"), registry, asyncio.get_running_loop())
    discovery.start()
    try:
        player = await until(lambda: registry.get(f"cast-{device.uuid}"), what="mDNS discovery")
        await until(lambda: player.online, what="the connection")
        assert player.name == "Whole house" and player.model == "Speaker group"
    finally:
        registry.close()
        discovery.close()
        await asyncio.to_thread(zc.close)
        device.close()
