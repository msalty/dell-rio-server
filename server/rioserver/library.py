"""Music library: scanning, persistence and queries.

Tracks live in SQLite so that IDs stay stable across restarts and rescans.
Receivers hold on to track IDs in their play queues, so an ID must never be
reused for a different file (hence AUTOINCREMENT).

Browsing is organised the way an iPod does it: tracks are grouped under their
*album artist* (falling back to the track artist), albums are ordered by year,
and album tracks are ordered by disc and track number.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3"}
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8"}
UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
UNKNOWN_GENRE = "Unknown"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sort TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id INTEGER NOT NULL,
    title TEXT NOT NULL COLLATE NOCASE,
    sort TEXT NOT NULL,
    year INTEGER,
    UNIQUE (artist_id, title)
);
CREATE TABLE IF NOT EXISTS genres (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sort TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    data_offset INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    title_sort TEXT NOT NULL,
    artist TEXT NOT NULL,
    artist_id INTEGER NOT NULL,
    album_id INTEGER NOT NULL,
    genre_id INTEGER NOT NULL,
    year INTEGER,
    track_no INTEGER,
    track_total INTEGER,
    disc_no INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    bitrate INTEGER NOT NULL DEFAULT 0,
    sample_rate INTEGER NOT NULL DEFAULT 44100,
    channels INTEGER NOT NULL DEFAULT 2,
    vbr INTEGER NOT NULL DEFAULT 0,
    added REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_id, disc_no, track_no);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS tracks_genre ON tracks(genre_id);
CREATE INDEX IF NOT EXISTS tracks_title ON tracks(title_sort);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL,
    pos INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, pos)
);
"""

_ARTICLES = ("the ", "a ", "an ")


def sort_key(name: str) -> str:
    """Case/accent-insensitive sort key that ignores a leading article."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold().strip()
    for art in _ARTICLES:
        if s.startswith(art) and len(s) > len(art):
            s = s[len(art):]
            break
    s = s.lstrip("\"'([{.-_ ")
    return s or (name or "").casefold()


def _first(tags, key: str) -> str:
    if not tags:
        return ""
    try:
        val = tags.get(key)
    except Exception:  # mutagen raises on some malformed frames
        return ""
    if not val:
        return ""
    if isinstance(val, (list, tuple)):
        val = val[0] if val else ""
    return str(val).strip().strip("\x00")


def _parse_number_pair(raw: str) -> tuple[int | None, int | None]:
    """Parse "3", "3/12" or "03 of 12" into (3, 12)."""
    if not raw:
        return None, None
    m = re.match(r"\s*(\d+)\s*(?:(?:/|of)\s*(\d+))?", raw)
    if not m:
        return None, None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


def _parse_year(raw: str) -> int | None:
    m = re.search(r"(\d{4})", raw or "")
    return int(m.group(1)) if m else None


def id3v2_size(path: Path) -> int:
    """Number of bytes of ID3v2 tag(s) at the start of the file."""
    offset = 0
    try:
        with open(path, "rb") as fh:
            while True:
                fh.seek(offset)
                hdr = fh.read(10)
                if len(hdr) < 10 or hdr[:3] != b"ID3":
                    break
                flags = hdr[5]
                size = 0
                for b in hdr[6:10]:
                    size = (size << 7) | (b & 0x7F)
                offset += 10 + size + (10 if flags & 0x10 else 0)
    except OSError:
        return 0
    return offset


_TRACK_PREFIX = re.compile(r"^\s*(\d{1,3})(?:\s*[-._)]\s*|\s+)(.+)$")


def guess_from_path(rel: Path) -> dict:
    """Fallback metadata from an Artist/Album/NN - Title.mp3 layout."""
    parts = rel.parts
    stem = rel.stem
    info: dict = {}
    m = _TRACK_PREFIX.match(stem)
    if m:
        info["track_no"] = int(m.group(1))
        stem = m.group(2)
    info["title"] = stem.replace("_", " ").strip()
    if len(parts) >= 3:
        info["artist"] = parts[-3]
        info["album"] = parts[-2]
    elif len(parts) == 2:
        info["album"] = parts[-2]
    return info


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: int = 0
    playlists: int = 0
    seconds: float = 0.0


def read_track_metadata(path: Path, rel: Path) -> dict | None:
    """Read tags and stream info from an MP3. Returns None if unreadable."""
    from mutagen.easyid3 import EasyID3
    from mutagen.mp3 import MP3, BitrateMode

    try:
        audio = MP3(str(path), ID3=EasyID3)
    except Exception as exc:  # HeaderNotFoundError and friends
        log.warning("skipping %s: %s", rel, exc)
        return None
    tags = audio.tags
    guess = guess_from_path(rel)
    track_no, track_total = _parse_number_pair(_first(tags, "tracknumber"))
    disc_no, _ = _parse_number_pair(_first(tags, "discnumber"))
    artist = _first(tags, "artist") or guess.get("artist") or UNKNOWN_ARTIST
    album_artist = _first(tags, "albumartist") or artist
    info = audio.info
    return {
        "title": _first(tags, "title") or guess.get("title") or rel.stem,
        "title_sort": _first(tags, "titlesort"),
        "artist": artist,
        "album_artist": album_artist,
        "album_artist_sort": _first(tags, "albumartistsort")
        or (_first(tags, "artistsort") if album_artist == artist else ""),
        "album": _first(tags, "album") or guess.get("album") or UNKNOWN_ALBUM,
        "album_sort": _first(tags, "albumsort"),
        "genre": _first(tags, "genre") or UNKNOWN_GENRE,
        "year": _parse_year(_first(tags, "date") or _first(tags, "originaldate")),
        "track_no": track_no if track_no is not None else guess.get("track_no"),
        "track_total": track_total,
        "disc_no": disc_no,
        "duration_ms": int(round((info.length or 0) * 1000)),
        "bitrate": int((info.bitrate or 0) // 1000),
        "sample_rate": int(info.sample_rate or 44100),
        "channels": int(info.channels or 2),
        "vbr": 1 if getattr(info, "bitrate_mode", None) == BitrateMode.VBR else 0,
        "data_offset": id3v2_size(path),
    }


TRACK_COLUMNS = (
    "t.id, t.path, t.size, t.data_offset, t.title, t.artist, t.artist_id, "
    "ar.name AS album_artist, t.album_id, al.title AS album, al.year AS album_year, "
    "t.genre_id, g.name AS genre, t.year, t.track_no, t.track_total, t.disc_no, "
    "t.duration_ms, t.bitrate, t.sample_rate, t.channels, t.vbr, t.added"
)
TRACK_JOIN = (
    "FROM tracks t JOIN artists ar ON ar.id = t.artist_id "
    "JOIN albums al ON al.id = t.album_id JOIN genres g ON g.id = t.genre_id"
)
ALBUM_ORDER = "COALESCE(t.disc_no, 1), COALESCE(t.track_no, 9999), t.title_sort"
ARTIST_TRACK_ORDER = (
    "COALESCE(al.year, 9999), al.sort, " + ALBUM_ORDER
)


class Library:
    def __init__(self, music_dir: Path, db_path: Path):
        self.music_dir = Path(music_dir)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._scan_lock = threading.Lock()
        self._listeners: list[Callable[[ScanResult], None]] = []
        self.version = 0
        self.scanning = False
        self.last_scan: ScanResult | None = None
        self.last_scan_time: float | None = None
        with self._conn() as c:
            c.executescript(SCHEMA)
        self.version = self._compute_version()

    # -- infrastructure -------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=OFF")
            self._local.conn = conn
        return conn

    def _q(self, sql: str, args: Iterable = ()) -> list[dict]:
        return [dict(r) for r in self._conn().execute(sql, tuple(args))]

    def _q1(self, sql: str, args: Iterable = ()) -> dict | None:
        row = self._conn().execute(sql, tuple(args)).fetchone()
        return dict(row) if row else None

    def _compute_version(self) -> int:
        row = self._q1("SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m, COALESCE(SUM(mtime),0) AS s FROM tracks")
        return hash((row["n"], row["m"], round(row["s"], 3))) & 0x7FFFFFFF

    def add_listener(self, fn: Callable[[ScanResult], None]) -> None:
        self._listeners.append(fn)

    # -- scanning -------------------------------------------------------
    def _get_id(self, c: sqlite3.Connection, table: str, name: str, sort: str) -> int:
        row = c.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
        if row:
            return row[0]
        return c.execute(f"INSERT INTO {table}(name, sort) VALUES (?, ?)", (name, sort)).lastrowid

    def _album_id(self, c: sqlite3.Connection, artist_id: int, title: str, sort: str, year) -> int:
        row = c.execute(
            "SELECT id, year FROM albums WHERE artist_id = ? AND title = ?", (artist_id, title)
        ).fetchone()
        if row:
            if year and not row[1]:
                c.execute("UPDATE albums SET year = ? WHERE id = ?", (year, row[0]))
            return row[0]
        return c.execute(
            "INSERT INTO albums(artist_id, title, sort, year) VALUES (?, ?, ?, ?)",
            (artist_id, title, sort, year),
        ).lastrowid

    def _store_track(self, c: sqlite3.Connection, rel: str, st: os.stat_result, md: dict, existing_id):
        artist_id = self._get_id(
            c, "artists", md["album_artist"], sort_key(md["album_artist_sort"] or md["album_artist"])
        )
        album_id = self._album_id(
            c, artist_id, md["album"], sort_key(md["album_sort"] or md["album"]), md["year"]
        )
        genre_id = self._get_id(c, "genres", md["genre"], sort_key(md["genre"]))
        values = (
            st.st_mtime, st.st_size, md["data_offset"], md["title"],
            sort_key(md["title_sort"] or md["title"]), md["artist"], artist_id, album_id,
            genre_id, md["year"], md["track_no"], md["track_total"], md["disc_no"],
            md["duration_ms"], md["bitrate"], md["sample_rate"], md["channels"], md["vbr"],
        )
        cols = (
            "mtime, size, data_offset, title, title_sort, artist, artist_id, album_id, genre_id, "
            "year, track_no, track_total, disc_no, duration_ms, bitrate, sample_rate, channels, vbr"
        )
        if existing_id is None:
            c.execute(
                f"INSERT INTO tracks(path, added, {cols}) VALUES (?, ?, {', '.join('?' * len(values))})",
                (rel, time.time(), *values),
            )
        else:
            sets = ", ".join(f"{col.strip()} = ?" for col in cols.split(","))
            c.execute(f"UPDATE tracks SET {sets} WHERE id = ?", (*values, existing_id))

    def scan(self, full: bool = False) -> ScanResult:
        """Walk the music directory and bring the database up to date.

        Unchanged files (same size and mtime) are skipped unless full=True.
        """
        if not self._scan_lock.acquire(blocking=False):
            log.info("scan already running; skipping")
            return self.last_scan or ScanResult()
        self.scanning = True
        start = time.time()
        res = ScanResult()
        try:
            c = self._conn()
            known = {
                r["path"]: (r["id"], r["mtime"], r["size"])
                for r in self._q("SELECT id, path, mtime, size FROM tracks")
            }
            seen: set[str] = set()
            playlist_files: list[tuple[Path, str, float]] = []
            if not self.music_dir.is_dir():
                log.error("music directory %s does not exist; is the volume mounted?", self.music_dir)
                return res
            pending = 0
            for root, dirs, files in os.walk(self.music_dir, followlinks=True):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files):
                    if name.startswith("."):
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    full_path = Path(root) / name
                    rel = full_path.relative_to(self.music_dir).as_posix()
                    if ext in PLAYLIST_EXTENSIONS:
                        try:
                            playlist_files.append((full_path, rel, full_path.stat().st_mtime))
                        except OSError:
                            pass
                        continue
                    if ext not in AUDIO_EXTENSIONS:
                        continue
                    try:
                        st = full_path.stat()
                    except OSError:
                        continue
                    seen.add(rel)
                    prev = known.get(rel)
                    if prev and not full and prev[1] == st.st_mtime and prev[2] == st.st_size:
                        res.unchanged += 1
                        continue
                    md = read_track_metadata(full_path, Path(rel))
                    if md is None:
                        res.errors += 1
                        continue
                    self._store_track(c, rel, st, md, prev[0] if prev else None)
                    if prev:
                        res.updated += 1
                    else:
                        res.added += 1
                    pending += 1
                    if pending >= 200:
                        c.commit()
                        pending = 0
            missing = [v[0] for k, v in known.items() if k not in seen]
            if missing and not seen:
                log.error(
                    "no audio files found under %s but the database has %d tracks; "
                    "refusing to delete them (is the volume mounted?)",
                    self.music_dir, len(missing),
                )
            elif missing:
                c.executemany("DELETE FROM tracks WHERE id = ?", [(i,) for i in missing])
                res.removed = len(missing)
            c.commit()
            res.playlists = self._scan_playlists(c, playlist_files)
            self._gc(c)
            c.commit()
        finally:
            res.seconds = round(time.time() - start, 2)
            self.scanning = False
            self._scan_lock.release()
        self.last_scan = res
        self.last_scan_time = time.time()
        changed = res.added or res.updated or res.removed or res.playlists
        if changed:
            self.version = self._compute_version()
        log.info(
            "scan finished in %.1fs: %d added, %d updated, %d removed, %d unchanged, %d errors",
            res.seconds, res.added, res.updated, res.removed, res.unchanged, res.errors,
        )
        for fn in self._listeners:
            try:
                fn(res)
            except Exception:
                log.exception("scan listener failed")
        return res

    def _scan_playlists(self, c: sqlite3.Connection, files: list[tuple[Path, str, float]]) -> int:
        by_path = {r["path"]: r["id"] for r in self._q("SELECT id, path FROM tracks")}
        existing = {r["path"]: (r["id"], r["mtime"]) for r in self._q("SELECT id, path, mtime FROM playlists")}
        changed = 0
        keep = set()
        for full_path, rel, mtime in files:
            keep.add(rel)
            prev = existing.get(rel)
            if prev and prev[1] == mtime:
                continue
            ids = []
            try:
                text = full_path.read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue
            base = full_path.parent
            for line in text.splitlines():
                line = line.strip().lstrip("﻿")
                if not line or line.startswith("#"):
                    continue
                entry = Path(line.replace("\\", "/"))
                cand = entry if entry.is_absolute() else (base / entry)
                try:
                    cand_rel = os.path.normpath(cand).replace(os.sep, "/")
                    cand_rel = os.path.relpath(cand_rel, self.music_dir).replace(os.sep, "/")
                except ValueError:
                    continue
                tid = by_path.get(cand_rel)
                if tid:
                    ids.append(tid)
            name = full_path.stem
            if prev:
                pid = prev[0]
                c.execute("UPDATE playlists SET name = ?, mtime = ? WHERE id = ?", (name, mtime, pid))
                c.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
            else:
                pid = c.execute(
                    "INSERT INTO playlists(path, name, mtime) VALUES (?, ?, ?)", (rel, name, mtime)
                ).lastrowid
            c.executemany(
                "INSERT INTO playlist_items(playlist_id, pos, track_id) VALUES (?, ?, ?)",
                [(pid, i, t) for i, t in enumerate(ids)],
            )
            changed += 1
        for rel, (pid, _) in existing.items():
            if rel not in keep:
                c.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
                c.execute("DELETE FROM playlists WHERE id = ?", (pid,))
                changed += 1
        return changed

    def _gc(self, c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM playlist_items WHERE track_id NOT IN (SELECT id FROM tracks)")
        c.execute("DELETE FROM albums WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks)")
        c.execute("DELETE FROM artists WHERE id NOT IN (SELECT DISTINCT artist_id FROM albums)")
        c.execute("DELETE FROM genres WHERE id NOT IN (SELECT DISTINCT genre_id FROM tracks)")
        # Album year = most common/earliest track year if the album has none.
        c.execute(
            "UPDATE albums SET year = (SELECT MIN(year) FROM tracks WHERE album_id = albums.id) "
            "WHERE year IS NULL"
        )

    # -- queries --------------------------------------------------------
    def stats(self) -> dict:
        row = self._q1(
            "SELECT COUNT(*) AS tracks, COALESCE(SUM(duration_ms),0) AS duration_ms, "
            "COALESCE(SUM(size),0) AS bytes FROM tracks"
        )
        row["artists"] = self._q1("SELECT COUNT(*) AS n FROM artists")["n"]
        row["albums"] = self._q1("SELECT COUNT(*) AS n FROM albums")["n"]
        row["genres"] = self._q1("SELECT COUNT(*) AS n FROM genres")["n"]
        row["playlists"] = self._q1("SELECT COUNT(*) AS n FROM playlists")["n"]
        return row

    def artists(self, prefix_regex: str | None = None) -> list[dict]:
        rows = self._q(
            "SELECT ar.id, ar.name, ar.sort, COUNT(DISTINCT t.album_id) AS album_count, "
            "COUNT(t.id) AS track_count FROM artists ar JOIN tracks t ON t.artist_id = ar.id "
            "GROUP BY ar.id ORDER BY ar.sort"
        )
        return _filter(rows, "name", prefix_regex)

    def artist(self, artist_id: int) -> dict | None:
        return self._q1("SELECT id, name FROM artists WHERE id = ?", (artist_id,))

    def artist_by_name(self, name: str) -> dict | None:
        return self._q1("SELECT id, name FROM artists WHERE name = ?", (name,))

    def albums(self, artist_id: int | None = None, genre_id: int | None = None,
               prefix_regex: str | None = None) -> list[dict]:
        where, args = [], []
        if artist_id is not None:
            where.append("al.artist_id = ?")
            args.append(artist_id)
        if genre_id is not None:
            where.append("t.genre_id = ?")
            args.append(genre_id)
        order = "COALESCE(al.year, 9999), al.sort" if artist_id is not None else "al.sort, ar.sort"
        rows = self._q(
            "SELECT al.id, al.title, al.year, al.artist_id, ar.name AS artist, "
            "COUNT(t.id) AS track_count, SUM(t.duration_ms) AS duration_ms, MIN(t.id) AS first_track "
            "FROM albums al JOIN artists ar ON ar.id = al.artist_id JOIN tracks t ON t.album_id = al.id "
            + ("WHERE " + " AND ".join(where) if where else "")
            + f" GROUP BY al.id ORDER BY {order}",
            args,
        )
        return _filter(rows, "title", prefix_regex)

    def album(self, album_id: int) -> dict | None:
        return self._q1(
            "SELECT al.id, al.title, al.year, al.artist_id, ar.name AS artist FROM albums al "
            "JOIN artists ar ON ar.id = al.artist_id WHERE al.id = ?",
            (album_id,),
        )

    def albums_by_title(self, title: str) -> list[dict]:
        return self._q("SELECT id, title, artist_id FROM albums WHERE title = ?", (title,))

    def genres(self, prefix_regex: str | None = None) -> list[dict]:
        rows = self._q(
            "SELECT g.id, g.name, COUNT(t.id) AS track_count FROM genres g "
            "JOIN tracks t ON t.genre_id = g.id GROUP BY g.id ORDER BY g.sort"
        )
        return _filter(rows, "name", prefix_regex)

    def genre(self, genre_id: int) -> dict | None:
        return self._q1("SELECT id, name FROM genres WHERE id = ?", (genre_id,))

    def genre_by_name(self, name: str) -> dict | None:
        return self._q1("SELECT id, name FROM genres WHERE name = ?", (name,))

    def genre_artists(self, genre_id: int) -> list[dict]:
        return self._q(
            "SELECT ar.id, ar.name, COUNT(DISTINCT t.album_id) AS album_count, COUNT(t.id) AS track_count "
            "FROM tracks t JOIN artists ar ON ar.id = t.artist_id WHERE t.genre_id = ? "
            "GROUP BY ar.id ORDER BY ar.sort",
            (genre_id,),
        )

    def tracks(self, *, album_id: int | None = None, artist_id: int | None = None,
               genre_id: int | None = None, title_regex: str | None = None,
               ids: list[int] | None = None) -> list[dict]:
        where, args = [], []
        if album_id is not None:
            where.append("t.album_id = ?")
            args.append(album_id)
        if artist_id is not None:
            where.append("t.artist_id = ?")
            args.append(artist_id)
        if genre_id is not None:
            where.append("t.genre_id = ?")
            args.append(genre_id)
        if album_id is not None:
            order = ALBUM_ORDER
        elif artist_id is not None or genre_id is not None:
            order = "ar.sort, " + ARTIST_TRACK_ORDER
        else:
            order = "t.title_sort"
        sql = f"SELECT {TRACK_COLUMNS} {TRACK_JOIN} " + (
            "WHERE " + " AND ".join(where) if where else ""
        ) + f" ORDER BY {order}"
        rows = self._q(sql, args)
        if ids is not None:
            pos = {tid: i for i, tid in enumerate(ids)}
            rows = sorted((r for r in rows if r["id"] in pos), key=lambda r: pos[r["id"]])
        return _filter(rows, "title", title_regex)

    def tracks_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        out: dict[int, dict] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._q(f"SELECT {TRACK_COLUMNS} {TRACK_JOIN} WHERE t.id IN ({marks})", chunk):
                out[r["id"]] = r
        return [out[i] for i in ids if i in out]

    def track(self, track_id: int) -> dict | None:
        return self._q1(f"SELECT {TRACK_COLUMNS} {TRACK_JOIN} WHERE t.id = ?", (track_id,))

    def track_path(self, track: dict) -> Path:
        return self.music_dir / track["path"]

    def playlists(self) -> list[dict]:
        return self._q(
            "SELECT p.id, p.name, COUNT(i.track_id) AS track_count FROM playlists p "
            "LEFT JOIN playlist_items i ON i.playlist_id = p.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE"
        )

    def playlist(self, playlist_id: int) -> dict | None:
        return self._q1("SELECT id, name FROM playlists WHERE id = ?", (playlist_id,))

    def playlist_tracks(self, playlist_id: int) -> list[dict]:
        ids = [r["track_id"] for r in self._q(
            "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY pos", (playlist_id,))]
        return self.tracks_by_ids(ids)

    def recently_added(self, limit: int = 100) -> list[dict]:
        return self._q(
            f"SELECT {TRACK_COLUMNS} {TRACK_JOIN} ORDER BY t.added DESC, t.album_id, "
            f"{ALBUM_ORDER} LIMIT ?",
            (limit,),
        )

    def all_track_ids(self) -> list[int]:
        return [r["id"] for r in self._q("SELECT id FROM tracks t ORDER BY t.title_sort")]

    def search(self, text: str, limit: int = 50) -> dict:
        like = f"%{text}%"
        return {
            "artists": self._q(
                "SELECT ar.id, ar.name, COUNT(t.id) AS track_count FROM artists ar "
                "JOIN tracks t ON t.artist_id = ar.id WHERE ar.name LIKE ? GROUP BY ar.id "
                "ORDER BY ar.sort LIMIT ?", (like, limit)),
            "albums": self._q(
                "SELECT al.id, al.title, al.year, ar.name AS artist, al.artist_id, "
                "MIN(t.id) AS first_track, COUNT(t.id) AS track_count FROM albums al "
                "JOIN artists ar ON ar.id = al.artist_id JOIN tracks t ON t.album_id = al.id "
                "WHERE al.title LIKE ? GROUP BY al.id ORDER BY al.sort LIMIT ?", (like, limit)),
            "tracks": self._q(
                f"SELECT {TRACK_COLUMNS} {TRACK_JOIN} WHERE t.title LIKE ? OR t.artist LIKE ? "
                "ORDER BY t.title_sort LIMIT ?", (like, like, limit)),
        }


def compile_prefix(pattern: str | None):
    """Turn a receiver search pattern into a compiled, anchored regex.

    The stock player sends things like "[5jkl][5jkl][4ghi]" for the remote's
    phone-keypad search. Anything that isn't a valid regex is matched as a
    literal prefix.
    """
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def _filter(rows: list[dict], key: str, prefix_regex: str | None) -> list[dict]:
    rx = compile_prefix(prefix_regex)
    if rx is None:
        return rows
    return [r for r in rows if rx.match(r[key] or "")]
