import struct

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rioserver import ids
from rioserver.app import Context, build_app
from rioserver.config import Config
from rioserver.receivers import ReceiverRegistry


@pytest.fixture()
async def client(library):
    ctx = Context(Config(), library, ReceiverRegistry())
    async with TestClient(TestServer(build_app(ctx))) as c:
        c.ctx = ctx
        yield c


def parse_tags(data: bytes) -> dict:
    out, i = {}, 0
    while data[i] != 0xFF:
        key, n = data[i], data[i + 1]
        out[key] = data[i + 2:i + 2 + n].decode("latin-1")
        i += 2 + n
    return out


def ng_lines(text: str) -> list[list[str]]:
    lines = text.split("\n")
    assert lines[0] == "RNG 1"
    return [line.split("\t") for line in lines[1:] if line]


# ---- classic protocol (stock firmware) ----------------------------------

async def test_tag_names(client):
    r = await client.get("/tags")
    assert (await r.text()).splitlines()[:3] == ["fid", "title", "artist"]


async def test_query_lists_artists(client):
    text = await (await client.get("/query?artist=")).text()
    lines = text.splitlines()
    assert lines[0] == "matches="
    assert lines[1] == "0=5,0,0:The Beatles"


async def test_query_prefix_search(client):
    # the remote's keypad search sends regex character classes
    text = await (await client.get("/query?artist=[a-c]")).text()
    assert "Björk" in text
    assert "Various" not in text


async def test_results_in_album_order(client):
    text = await (await client.get("/results?source=Abbey%20Road&_extended=1")).text()
    titles = [line.split("=T", 1)[1] for line in text.splitlines()]
    assert titles == ["Come Together", "Something", "Maxwell's Silver Hammer"]


async def test_results_binary(client, library):
    data = await (await client.get("/results?artist=The%20Beatles&_extended=2")).read()
    recs = [struct.unpack_from("<III", data, i) for i in range(0, len(data), 12)]
    assert len(recs) == 5
    t = library.track(ids.decode_fid(recs[0][0])[1])
    assert t["title"] == "Taxman"
    assert recs[0][1] == t["size"] - t["data_offset"]


async def test_track_tags(client, library):
    t = library.tracks()[0]
    data = await (await client.get(f"/tags/{ids.track_fid(t['id']):x}")).read()
    tags = parse_tags(data)
    assert tags[1] == t["title"]
    assert tags[7] == "tune"
    assert tags[12] == "mp3"


async def test_playlists_include_albums(client):
    text = await (await client.get("/content/100?_extended=1")).text()
    names = [line.split("=P", 1)[1] for line in text.splitlines()]
    assert names[0] == "Road Trip"
    assert "The Beatles - Abbey Road" in names


async def test_content_range_like_original_server(client, library):
    t = library.tracks()[0]
    r = await client.get(f"/content/{ids.track_fid(t['id']):x}", headers={"Range": "bytes=0-99"})
    assert r.status == 206
    assert r.headers["Content-Range"] == "bytes=0-99/*"
    body = await r.read()
    assert len(body) == 100 and body[0] == 0xFF  # ID3 tag skipped


async def test_layouts(client):
    text = await (await client.get("/layout/en_UK/index")).text()
    assert "/layout/en_UK/all_info=All Info" in text
    data = await (await client.get("/layout/en_UK/all_info")).read()
    assert len(data) % 4 == 0 and data[:4] == b"\x02\x00\x00\x00"


# ---- NG protocol (RioServer NG firmware) --------------------------------

async def test_ng_root_menu(client):
    rows = ng_lines(await (await client.get("/ng/menu/root")).text())
    labels = [r[3] for r in rows if r[0] == "i"]
    assert labels[:4] == ["Artists", "Albums", "Songs", "Genres"]


async def test_ng_artist_album_track_hierarchy(client, library):
    beatles = library.artist_by_name("The Beatles")
    rows = ng_lines(await (await client.get(f"/ng/menu/ar:{beatles['id']}")).text())
    items = [r for r in rows if r[0] == "i"]
    assert [i[3] for i in items] == ["All Songs", "Revolver", "Abbey Road"]
    abbey_ref = items[2][2]
    rows = ng_lines(await (await client.get(f"/ng/menu/{abbey_ref}")).text())
    tracks = [r for r in rows if r[0] == "i"]
    assert [t[1] for t in tracks] == ["T", "T", "T"]
    assert [t[3] for t in tracks] == ["Come Together", "Something", "Maxwell's Silver Hammer"]
    assert [t[4] for t in tracks] == ["1", "2", "3"]


async def test_ng_menu_paging(client):
    rows = ng_lines(await (await client.get("/ng/menu/songs?b=4&n=3")).text())
    d = {r[0]: r[1] for r in rows if r[0] != "i"}
    assert d["total"] == "10" and d["begin"] == "4"
    assert len([r for r in rows if r[0] == "i"]) == 3


async def test_ng_queue_starts_at_selection(client, library):
    abbey = library.albums_by_title("Abbey Road")[0]
    rows = ng_lines(await (await client.get(f"/ng/queue/al:{abbey['id']}?first=1")).text())
    fids = [int(r[0], 16) for r in rows[1:]]
    titles = [library.track(ids.decode_fid(f)[1])["title"] for f in fids]
    assert titles == ["Something", "Maxwell's Silver Hammer"]


async def test_ng_queue_shuffle_keeps_first(client, library):
    rows = ng_lines(await (await client.get("/ng/queue/songs?first=3&shuffle=1")).text())
    fids = [r[0] for r in rows[1:]]
    songs = ng_lines(await (await client.get("/ng/menu/songs")).text())
    assert fids[0] == [r for r in songs if r[0] == "i"][3][2]
    assert len(set(fids)) == 10


async def test_ng_track_info(client, library):
    t = library.albums_by_title("Homogenic")[0]
    tr = library.tracks(album_id=t["id"])[1]
    rows = dict(ng_lines(await (await client.get(f"/ng/track/{ids.track_fid(tr['id']):x}")).text()))
    assert rows["title"] == "Jóga"
    assert rows["track"] == "2" and rows["tracks"] == "2"


async def test_web_play_reaches_receiver_poll(client, library):
    r = await client.get("/ng/poll?mac=00:90:4b:00:00:01&st=stop")
    assert r.status == 200
    ip = next(iter(client.ctx.receivers._by_ip))
    beatles = library.artist_by_name("The Beatles")
    resp = await client.post(f"/api/receivers/{ip}/play", json={"artist_id": beatles["id"]})
    assert resp.status == 200
    rows = ng_lines(await (await client.get("/ng/poll?st=stop")).text())
    cmds = [r for r in rows if r[0] == "cmd"]
    assert cmds == [["cmd", "play", f"ar:{beatles['id']}/all", "0", "0"]]
    receivers = await (await client.get("/api/receivers")).json()
    assert receivers[0]["firmware"] == "ng" and receivers[0]["mac"] == "00:90:4b:00:00:01"


# ---- web API ------------------------------------------------------------

async def test_api_album(client, library):
    abbey = library.albums_by_title("Abbey Road")[0]
    data = await (await client.get(f"/api/albums/{abbey['id']}")).json()
    assert data["artist"] == "The Beatles"
    assert [t["track_no"] for t in data["tracks"]] == [1, 2, 3]


async def test_api_stream_uses_standard_ranges(client, library):
    t = library.tracks()[0]
    r = await client.get(f"/api/stream/{t['id']}", headers={"Range": "bytes=0-9"})
    assert r.status == 206
    assert r.headers["Content-Range"] == f"bytes 0-9/{t['size']}"
    assert (await r.read())[:3] == b"ID3"


async def test_api_art_from_folder_jpg(client, library):
    abbey = library.albums_by_title("Abbey Road")[0]
    r = await client.get(f"/api/art/album/{abbey['id']}")
    assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg"
    r = await client.get(f"/api/art/album/{library.albums_by_title('Revolver')[0]['id']}")
    assert r.status == 404


async def test_api_search(client):
    res = await (await client.get("/api/search?q=so")).json()
    assert {t["title"] for t in res["tracks"]} >= {"So What", "Something"}


async def test_index_page(client):
    r = await client.get("/")
    assert r.status == 200 and "RioServer" in await r.text()


async def test_firmware_listing_select_and_download(library, tmp_path):
    from rioserver.firmware import FirmwareStore
    from tests.test_boot import make_arf

    builtin, user = tmp_path / "builtin", tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    (builtin / "receiver-ng.arf").write_bytes(make_arf())
    (user / "receiver.arf").write_bytes(make_arf())
    ctx = Context(Config(), library, ReceiverRegistry())
    ctx.firmware = FirmwareStore([user, builtin], "ng")
    async with TestClient(TestServer(build_app(ctx))) as c:
        info = await (await c.get("/api/firmware")).json()
        assert {a["name"] for a in info["available"]} == {"ng", "receiver.arf"}
        assert (await c.post("/api/firmware", json={"name": "receiver.arf"})).status == 200
        assert ctx.firmware.path() == user / "receiver.arf"
        assert (await c.post("/api/firmware", json={"name": "../etc/passwd"})).status == 400
        r = await c.get("/api/firmware/ng/download")
        assert r.status == 200 and (await r.read()) == make_arf()


async def test_installable_web_app(client):
    # The service worker must come from the root to control the whole site.
    r = await client.get("/sw.js")
    assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
    manifest = await (await client.get("/static/manifest.json")).json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        assert (await client.get(icon["src"])).status == 200
    assert 'rel="manifest"' in await (await client.get("/")).text()


# ---- remote control from the web interface ------------------------------

async def _ng_receiver(client, wake=0):
    await client.get(f"/ng/poll?mac=00:90:4b:00:00:01&st=play&vol=40&wake={wake}")
    return next(iter(client.ctx.receivers._by_ip))


async def test_volume_drags_collapse_to_latest_value(client):
    ip = await _ng_receiver(client)
    for v in (45, 52, 60, 71):
        assert (await client.post(f"/api/receivers/{ip}/command", json={"cmd": "vol", "value": v})).status == 200
    await client.post(f"/api/receivers/{ip}/command", json={"cmd": "next"})
    # the web interface sees the new volume straight away...
    rx = (await (await client.get("/api/receivers")).json())[0]
    assert rx["state"]["vol"] == "71"
    # ...the receiver gets only the last value, in order with other commands...
    rows = ng_lines(await (await client.get("/ng/poll?st=play&vol=40")).text())
    assert [r[1:] for r in rows if r[0] == "cmd"] == [["vol", "71"], ["next"]]
    # ...and the poll that carried the command doesn't bounce the slider back
    rx = (await (await client.get("/api/receivers")).json())[0]
    assert rx["state"]["vol"] == "71"


async def test_commands_wake_the_receiver(client):
    import asyncio
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    try:
        ip = await _ng_receiver(client, wake=sock.getsockname()[1])
        await client.post(f"/api/receivers/{ip}/command", json={"cmd": "toggle"})
        data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(sock, 64), 2)
        assert data == b"poll"
    finally:
        sock.close()


async def test_receiver_names_are_remembered(library, tmp_path):
    names = tmp_path / "receivers.json"
    ctx = Context(Config(), library, ReceiverRegistry(names))
    async with TestClient(TestServer(build_app(ctx))) as c:
        c.ctx = ctx
        ip = await _ng_receiver(c)
        r = await c.post(f"/api/receivers/{ip}/name", json={"name": "  Kitchen   shelf "})
        assert (await r.json())["name"] == "Kitchen shelf"
    # a new server process, and the receiver came back with another address
    reg = ReceiverRegistry(names)
    assert reg.touch("10.0.0.99", mac="00:90:4B:00:00:01").name == "Kitchen shelf"
    reg.rename(reg.get("10.0.0.99"), "")
    assert reg.get("10.0.0.99").name == "Receiver 00:00:01"


async def test_booted_receiver_polling_through_a_proxy_is_one_receiver(client):
    reg = client.ctx.receivers
    # it boots from its LAN address: NFS mount (no MAC), then SSDP (with MAC)...
    reg.touch("192.168.1.40", stage="booting")
    reg.touch("192.168.1.40", mac="00:90:4B:00:00:01", stage="booting")
    # ...then the firmware's HTTP requests arrive from a proxy on loopback
    await client.get("/ng/poll?mac=00:90:4b:00:00:01&st=play&vol=40")
    await client.get("/ng/menu/root")
    rxs = await (await client.get("/api/receivers")).json()
    assert len(rxs) == 1
    assert rxs[0]["ip"] == "192.168.1.40"  # shown, and where wake-ups go
    assert rxs[0]["stage"] == "running" and rxs[0]["controllable"]
    await client.post("/api/receivers/192.168.1.40/command", json={"cmd": "next"})
    rows = ng_lines(await (await client.get("/ng/poll?mac=00:90:4b:00:00:01&st=play")).text())
    assert ["cmd", "next"] in rows
    # the next boot has a new DHCP address: still the same card
    reg.touch("192.168.1.41", mac="00:90:4b:00:00:01", stage="booting")
    assert [r.ip for r in reg.all()] == ["192.168.1.41"]
    assert reg.get("192.168.1.40") is None and reg.get("127.0.0.1") is reg.get("192.168.1.41")
    # a different unit given the old address gets its own card
    reg.touch("192.168.1.41", mac="00:90:4b:00:00:02", stage="booting")
    assert sorted(r.mac for r in reg.all()) == ["00:90:4b:00:00:01", "00:90:4b:00:00:02"]


async def test_receivers_are_addressed_by_mac(client):
    reg = client.ctx.receivers
    reg.touch("192.168.1.40", mac="00:90:4B:00:00:01", firmware="ng", stage="running")
    rx = (await (await client.get("/api/receivers")).json())[0]
    assert rx["id"] == "00:90:4b:00:00:01" and rx["ip"] == "192.168.1.40"
    # the router hands it a new address; the id the web interface saved still works
    reg.touch("192.168.1.41", mac="00:90:4b:00:00:01")
    r = await client.post("/api/receivers/00:90:4b:00:00:01/command", json={"cmd": "next"})
    assert r.status == 200
    # so does its current address, for anything that still uses one
    r = await client.post("/api/receivers/192.168.1.41/command", json={"cmd": "toggle"})
    assert r.status == 200
    rows = ng_lines(await (await client.get("/ng/poll?mac=00:90:4b:00:00:01&st=play")).text())
    assert [r[1:] for r in rows if r[0] == "cmd"] == [["next"], ["toggle"]]
    # until it reports a MAC, a receiver is known by its address
    assert reg.touch("192.168.1.50").id == "192.168.1.50"


async def test_sleep_timer_fades_out_and_restores_volume(client, monkeypatch):
    import asyncio

    from rioserver import web_api

    monkeypatch.setattr(web_api, "SLEEP_FADE", 0.1)
    ip = await _ng_receiver(client)
    r = await client.post(f"/api/receivers/{ip}/sleep", json={"minutes": 60})
    assert (await r.json())["sleep_at"] is not None
    r = await client.post(f"/api/receivers/{ip}/sleep", json={"minutes": 0})
    assert (await r.json())["sleep_at"] is None
    await client.post(f"/api/receivers/{ip}/sleep", json={"minutes": 0.001})
    await asyncio.sleep(0.5)
    rows = ng_lines(await (await client.get("/ng/poll?st=play&vol=0")).text())
    assert [r[1:] for r in rows if r[0] == "cmd"] == [["pause"], ["vol", "40"]]
    assert client.ctx.receivers.get(ip).sleep_at is None


async def test_artist_art(client, library):
    beatles = library.artist_by_name("The Beatles")
    r = await client.get(f"/api/art/artist/{beatles['id']}")  # falls back to Abbey Road's cover
    assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg"
    assert (await client.get(f"/api/art/artist/{library.artist_by_name('Björk')['id']}")).status == 404


async def test_artist_photo_beats_album_covers(music_dir, tmp_path):
    import shutil

    from rioserver.library import Library

    music = tmp_path / "music"
    shutil.copytree(music_dir, music)
    (music / "Björk" / "artist.png").write_bytes(b"\x89PNG fake")
    lib = Library(music, tmp_path / "lib.db")
    lib.scan()
    ctx = Context(Config(), lib, ReceiverRegistry())
    async with TestClient(TestServer(build_app(ctx))) as c:
        r = await c.get(f"/api/art/artist/{lib.artist_by_name('Björk')['id']}")
        assert r.status == 200 and r.headers["Content-Type"] == "image/png"
        assert await r.read() == b"\x89PNG fake"
