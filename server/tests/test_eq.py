"""The equaliser and balance applied on the server (eq.py)."""

import shutil
import subprocess

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rioserver import eq, ids
from rioserver.app import Context, build_app
from rioserver.config import Config
from rioserver.history import PlayTracker
from rioserver.receivers import ReceiverRegistry

BASS = {"on": True, "preamp": -3, "bands": [9, 6, 3, 0, 0, 0, 0, 0, 0, 0], "bal": 0}


@pytest.fixture()
async def client(library, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed for the equaliser")
    ctx = Context(Config(data_dir=tmp_path), library, ReceiverRegistry(tmp_path / "receivers.json", eq.EqStore(tmp_path / "eq.json")))
    ctx.tracker = PlayTracker(library)
    c = TestClient(TestServer(build_app(ctx)))
    c.ctx = ctx
    await c.start_server()
    yield c
    await c.close()


def decoded_seconds(data: bytes) -> float:
    """How much sound ffmpeg finds in these MP3 bytes."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1",
                          "-ar", "8000", "pipe:1"], input=data, capture_output=True, check=True).stdout
    return len(out) / 16000


def test_settings_are_cleaned_and_survive_a_url():
    s = eq.clean({"on": 1, "preamp": 40, "bands": [3, "x", -99], "bal": -2})
    assert s == {"on": True, "preamp": 12.0, "bands": [3.0, 0.0, -12.0] + [0.0] * 7, "bal": -1.0}
    assert eq.decode(eq.encode(s)) == s
    assert eq.decode("1,2,3") is None and eq.decode("") is None
    assert not eq.active(eq.FLAT) and not eq.active({**BASS, "on": False})
    assert eq.active(eq.clean(BASS)) and eq.active(eq.clean({"bal": 0.5}))  # balance works with the equaliser off


def test_filters():
    assert eq.filters(eq.clean({}), 2, 44100) == "anull"
    chain = eq.filters(eq.clean(BASS), 2, 44100)
    assert chain.startswith("volume=-3.0dB,equalizer=f=60:t=q:w=1.0:g=9.0,") and chain.endswith("alimiter=limit=0.97:level=disabled")
    # The balance turns one side down; a mono track becomes two channels for it.
    assert eq.filters(eq.clean({"bal": 0.5}), 2, 44100) == "pan=stereo|c0=0.500*c0|c1=1.000*c1"
    assert eq.filters(eq.clean({"bal": -1}), 1, 44100) == "pan=stereo|c0=1.000*c0|c1=0.000*c0"
    # Bands above what the track's sample rate can hold are left out.
    high = eq.filters(eq.clean({"on": True, "bands": [0] * 9 + [6]}), 2, 22050)
    assert "equalizer" not in high


def test_plan():
    t = {"duration_ms": 200_000, "size": 200 * 187 * 125 + 1000, "data_offset": 1000, "sample_rate": 44100}
    # A browser sees a constant-bitrate file at least as good as the original...
    assert eq.plan(t, exact=True) == (192, 200 * 192 * 125)
    # ...a receiver keeps the size it was told, at the best bitrate that fits in it.
    assert eq.plan(t, exact=False) == (160, 200 * 187 * 125)
    cbr = {**t, "size": 200 * 128 * 125 + 1000}
    assert eq.plan(cbr, exact=True)[0] == 128 and eq.plan(cbr, exact=False)[0] == 128
    assert eq.plan({**t, "duration_ms": 0}, exact=True) is None


async def test_stream_with_settings_is_a_seekable_file(client, library):
    t = library.tracks()[0]
    url = f"/api/stream/{t['id']}?eq={eq.encode(eq.clean(BASS))}"
    kbps, size = eq.plan(t, exact=True)
    r = await client.get(url)
    body = await r.read()
    assert r.status == 200 and len(body) == size == int(r.headers["Content-Length"])
    assert abs(decoded_seconds(body) - t["duration_ms"] / 1000) < 0.2
    # A range is that part of the same file: the second half holds half the sound.
    r = await client.get(url, headers={"Range": f"bytes={size // 2}-"})
    half = await r.read()
    assert r.status == 206 and r.headers["Content-Range"] == f"bytes {size // 2}-{size - 1}/{size}"
    assert len(half) == size - size // 2
    assert abs(decoded_seconds(half) - t["duration_ms"] / 2000) < 0.2
    assert (await client.get(url, headers={"Range": f"bytes={size}-"})).status == 416
    # Flat settings, or none, are the file itself.
    plain = await (await client.get(f"/api/stream/{t['id']}")).read()
    assert await (await client.get(f"/api/stream/{t['id']}?eq={eq.encode(eq.FLAT)}")).read() == plain
    assert len(plain) == t["size"]


async def test_a_receiver_gets_its_tracks_filtered_at_the_size_it_knows(client, library):
    reg = client.ctx.receivers
    r = reg.touch("127.0.0.1", firmware="ng", stage="running", mac="00:90:00:11:22:33")
    t = library.tracks()[0]
    path = f"/content/{ids.track_fid(t['id']):x}"
    plain = await (await client.get(path)).read()
    assert len(plain) == t["size"] - t["data_offset"]

    resp = await client.post(f"/api/receivers/{r.id}/eq", json=BASS)
    assert resp.status == 200 and (await resp.json())["bands"][0] == 9
    listed = next(x for x in await (await client.get("/api/receivers")).json() if x["id"] == r.id)
    assert listed["eq"]["on"] and listed["eq"]["preamp"] == -3
    filtered = await (await client.get(path)).read()
    assert len(filtered) == len(plain) and filtered != plain
    assert abs(decoded_seconds(filtered) - t["duration_ms"] / 1000) < 0.2
    # It asks for a place in the track the way the firmware does: time x size / duration.
    start = len(plain) // 4
    part = await client.get(path, headers={"Range": f"bytes={start}-"})
    tail = await part.read()
    assert part.status == 206 and len(tail) == len(plain) - start
    assert abs(decoded_seconds(tail) - t["duration_ms"] * 0.75 / 1000) < 0.2

    # While it plays, a change makes it fetch the track again from where it is.
    r.state.update(st="play", id=f"{ids.track_fid(t['id']):x}", pos="700")
    r.commands.clear()
    await client.post(f"/api/receivers/{r.id}/eq", json={**BASS, "bal": 0.5})
    assert ("seek", 700) in r.commands
    # Settings are kept, and flat ones are the file again.
    assert eq.EqStore(reg.eq._path).get(r.key)["bal"] == 0.5
    await client.post(f"/api/receivers/{r.id}/eq", json={})
    assert await (await client.get(path)).read() == plain


async def test_a_stock_receiver_is_refused(client):
    r = client.ctx.receivers.touch("10.0.0.9", firmware="stock", stage="running")
    assert (await client.post(f"/api/receivers/{r.id}/eq", json=BASS)).status == 409
