"""A browser as a receiver: the server feeds it over a WebSocket."""

import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rioserver import players
from rioserver.app import Context, build_app
from rioserver.config import Config
from rioserver.history import PlayTracker
from rioserver.receivers import ReceiverRegistry
from rioserver.webplayer import device_model

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7 like Mac OS X) AppleWebKit/605.1.15 Version/16.6 Mobile/15E148 Safari/604.1"
FIRE = "Mozilla/5.0 (Linux; Android 9; KFTRWI) AppleWebKit/537.36 (KHTML, like Gecko) Silk/120.4.1 like Chrome/120.0 Safari/537.36"


@pytest.fixture()
async def client(library, tmp_path):
    ctx = Context(Config(data_dir=tmp_path), library, ReceiverRegistry(tmp_path / "receivers.json"))
    ctx.tracker = PlayTracker(library)
    ctx.receivers.start_position = ctx.tracker.start_position
    async with TestClient(TestServer(build_app(ctx))) as c:
        c.ctx = ctx
        yield c
        ctx.receivers.close()


class Browser:
    """The page's side of the connection."""

    def __init__(self, ws):
        self.ws = ws
        self.inbox: list[dict] = []

    async def send(self, **msg):
        await self.ws.send_str(json.dumps(msg))

    async def expect(self, kind: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            for i, m in enumerate(self.inbox):
                if m["type"] == kind:
                    return self.inbox.pop(i)
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"no {kind!r} message; got {self.inbox}")
            try:
                msg = await self.ws.receive(timeout=left)
            except asyncio.TimeoutError:
                continue
            self.inbox.append(json.loads(msg.data))


async def connect(client, cid="abc123", ua=IPHONE, name="Living Room", volume=False) -> Browser:
    ws = await client.ws_connect(f"/api/player/ws?id={cid}", headers={"User-Agent": ua})
    b = Browser(ws)
    welcome = await b.expect("welcome")
    assert welcome["id"] == f"web-{cid}"
    await b.send(type="hello", name=name, volume=volume)
    return b


async def receivers(client) -> list[dict]:
    return await (await client.get("/api/receivers")).json()


async def until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not (v := await predicate() if asyncio.iscoroutinefunction(predicate) else predicate()):
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)
    return v


def test_device_names():
    assert device_model(IPHONE) == "iPhone"
    assert device_model(FIRE) == "Fire tablet"
    assert device_model("Mozilla/5.0 (Linux; Android 13; Pixel 7) Chrome/120") == "Android"


async def test_browser_shows_up_as_a_receiver_and_plays(client, library):
    b = await connect(client)

    async def listed():
        return next((r for r in await receivers(client) if r["id"] == "web-abc123"), None)
    rx = await until(listed)
    assert rx["kind"] == "browser" and rx["model"] == "iPhone" and rx["online"] and rx["controllable"]
    assert rx["volume"] is False  # Safari on iPhone can't set its own volume
    await until(lambda: client.ctx.receivers.get("web-abc123").name == "Living Room")

    album = next(a for a in library.albums() if a["title"] == "Abbey Road")
    tracks = library.tracks(album_id=album["id"])
    r = await client.post("/api/receivers/web-abc123/play", json={"album_id": album["id"]})
    assert r.status == 200
    load = await b.expect("load")
    assert load["url"] == f"/api/stream/{tracks[0]['id']}" and load["track"]["title"] == "Come Together"
    assert load["start"] == 0
    # The page can fetch what it was told to play.
    assert (await client.get(load["url"])).status == 200

    # It plays: the server hands it the next track to go on to without a gap.
    await b.send(type="status", st="play", pos=100, track_id=tracks[0]["id"])
    pre = await b.expect("preload")
    assert pre["track"]["title"] == "Something"
    rx = await listed()
    assert rx["state"]["st"] == "play" and rx["state"]["track"]["title"] == "Come Together"

    # It went on to the preloaded track by itself: the queue follows.
    await b.send(type="status", st="play", pos=0, track_id=tracks[1]["id"])
    await until(lambda: client.ctx.receivers.get("web-abc123").index == 1)
    assert (await b.expect("preload"))["track"]["title"] == "Maxwell's Silver Hammer"

    # Commands reach it.
    for body, kind in (({"cmd": "toggle"}, "pause"), ({"cmd": "seek", "value": 500}, "seek")):
        assert (await client.post("/api/receivers/web-abc123/command", json=body)).status == 200
        msg = await b.expect(kind)
    assert msg["to"] == 0.5

    # At the end of the last track it says so, and the queue stops.
    await client.post("/api/receivers/web-abc123/queue", json={"op": "jump", "pos": 2})
    await b.expect("load")
    await b.send(type="status", st="play", pos=10, track_id=tracks[2]["id"])
    await b.send(type="ended", track_id=tracks[2]["id"])
    await until(lambda: client.ctx.receivers.get("web-abc123").state["st"] == "stop")


async def test_browser_volume_and_reconnect(client, library):
    b = await connect(client, cid="fire1", ua=FIRE, name="Kitchen Fire", volume=True)
    rx = await until(lambda: client.ctx.receivers.get("web-fire1"))
    await until(lambda: rx.name == "Kitchen Fire")
    assert rx.to_json()["volume"] is True and rx.model == "Fire tablet"
    await client.post("/api/receivers/web-fire1/command", json={"cmd": "vol", "value": 40})
    assert (await b.expect("volume"))["level"] == 0.4

    # The page goes away (a reload, the screen locking): offline, but remembered.
    await b.ws.close()
    await until(lambda: not rx.online)
    assert any(r["id"] == "web-fire1" and not r["online"] for r in await receivers(client))
    # It comes back as the same receiver, with its name.
    b = await connect(client, cid="fire1", ua=FIRE, name="Kitchen Fire", volume=True)
    await until(lambda: rx.online)
    assert client.ctx.receivers.get("web-fire1") is rx
    # Only an offline browser can be forgotten.
    assert (await client.delete("/api/receivers/web-fire1")).status == 409
    await b.ws.close()
    await until(lambda: not rx.online)
    assert (await client.delete("/api/receivers/web-fire1")).status == 200
    assert all(r["id"] != "web-fire1" for r in await receivers(client))


async def test_a_disconnected_browser_does_not_hold_things_up(client, library, monkeypatch):
    b = await connect(client, cid="gone")
    await b.ws.close()
    p = client.ctx.receivers.get("web-gone")
    await until(lambda: not p.online)
    album = next(a for a in library.albums() if a["title"] == "Revolver")
    r = await client.post("/api/receivers/web-gone/play", json={"album_id": album["id"]})
    assert r.status == 200  # accepted; the device call fails quietly on the player's own thread
    await asyncio.sleep(0.2)
    assert isinstance(p, players.QueuePlayer)
