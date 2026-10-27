import os
import shutil

from rioserver.library import Library, guess_from_path, sort_key
from pathlib import Path


def test_scan_counts(library):
    st = library.stats()
    assert st["tracks"] == 10
    assert st["albums"] == 4
    assert st["artists"] == 3  # Various Artists groups the compilation
    assert st["playlists"] == 1


def test_artists_sorted_ignoring_articles(library):
    names = [a["name"] for a in library.artists()]
    assert names == ["The Beatles", "Björk", "Various Artists"]


def test_albums_by_artist_in_release_order(library):
    beatles = library.artist_by_name("The Beatles")
    assert [a["title"] for a in library.albums(artist_id=beatles["id"])] == ["Revolver", "Abbey Road"]


def test_album_tracks_in_track_order_not_alphabetical(library):
    abbey = library.albums_by_title("Abbey Road")[0]
    titles = [t["title"] for t in library.tracks(album_id=abbey["id"])]
    assert titles == ["Come Together", "Something", "Maxwell's Silver Hammer"]


def test_artist_tracks_follow_album_then_track_order(library):
    beatles = library.artist_by_name("The Beatles")
    titles = [t["title"] for t in library.tracks(artist_id=beatles["id"])]
    assert titles == ["Taxman", "Eleanor Rigby", "Come Together", "Something", "Maxwell's Silver Hammer"]


def test_compilation_keeps_track_artists(library):
    jazz = library.albums_by_title("Jazz Classics")[0]
    tracks = library.tracks(album_id=jazz["id"])
    assert [t["artist"] for t in tracks] == ["Miles Davis", "Dave Brubeck", "John Coltrane"]
    assert {t["album_artist"] for t in tracks} == {"Various Artists"}


def test_data_offset_points_at_mpeg_frame(library):
    for t in library.tracks():
        assert t["data_offset"] > 0
        with open(library.track_path(t), "rb") as f:
            f.seek(t["data_offset"])
            head = f.read(2)
        assert head[0] == 0xFF and head[1] & 0xE0 == 0xE0, t["path"]


def test_playlist_resolves_relative_paths(library):
    pl = library.playlists()[0]
    assert pl["name"] == "Road Trip"
    assert [t["title"] for t in library.playlist_tracks(pl["id"])] == ["So What", "Something"]


def test_rescan_keeps_ids_and_picks_up_changes(music_dir, tmp_path):
    work = tmp_path / "music"
    shutil.copytree(music_dir, work)
    lib = Library(work, tmp_path / "db.sqlite")
    lib.scan()
    ids = {t["path"]: t["id"] for t in lib.tracks()}
    version = lib.version

    victim = work / "Björk" / "Homogenic" / "Hunter.mp3"
    victim.unlink()
    copy = work / "Björk" / "Homogenic" / "Hunter (Live).mp3"
    shutil.copy(work / "Björk" / "Homogenic" / "Jóga.mp3", copy)
    res = lib.scan()
    assert (res.added, res.removed) == (1, 1)
    after = {t["path"]: t["id"] for t in lib.tracks()}
    for path, tid in after.items():
        if path in ids:
            assert ids[path] == tid
    assert max(after.values()) > max(ids.values())  # deleted IDs are never reused
    assert lib.version != version


def test_missing_volume_does_not_wipe_library(music_dir, tmp_path):
    work = tmp_path / "music"
    shutil.copytree(music_dir, work)
    lib = Library(work, tmp_path / "db.sqlite")
    lib.scan()
    for p in work.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    lib.scan()
    assert lib.stats()["tracks"] == 10


def test_untagged_files_fall_back_to_folder_names():
    info = guess_from_path(Path("Pixies/Doolittle/07 - Hey.mp3"))
    assert info == {"track_no": 7, "title": "Hey", "artist": "Pixies", "album": "Doolittle"}


def test_sort_key():
    assert sort_key("The Beatles") == "beatles"
    assert sort_key("Björk") == "bjork"
    assert sort_key("The") == "the"
