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
