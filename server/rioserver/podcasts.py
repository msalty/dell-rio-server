"""Podcasts: subscribe to a feed, download its episodes, remember where each
was left off.

The receivers can't fetch podcasts themselves: they speak plain HTTP and
nearly every feed and episode is served over HTTPS now. So the server
downloads episodes into <data>/podcasts/<podcast id>/ and adds each one to
the library as a track (marked with its podcast_id, which keeps it out of
the music menus). Every player then streams it like any other track, with
seeking, and the play history and queue work for it as well.

Only MP3 episodes are kept, because that is all the receivers can decode.
Nearly all podcasts are MP3; the few that aren't are listed as unsupported.

Each show keeps its newest few episodes downloaded ("keep"). Episodes
downloaded by hand stay until they have been played. Played episodes are
deleted a day after they were finished.
"""

from __future__ import annotations

import asyncio
import email.utils
import html
import logging
import re
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS podcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    keep INTEGER NOT NULL DEFAULT 3,
    added REAL NOT NULL,
    checked REAL,
    error TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast_id INTEGER NOT NULL,
    guid TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    published REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    error TEXT NOT NULL DEFAULT '',
    track_id INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0,
    position_ms INTEGER NOT NULL DEFAULT 0,
    played INTEGER NOT NULL DEFAULT 0,
    played_at REAL,
    downloaded_at REAL,
    UNIQUE (podcast_id, guid)
);
CREATE INDEX IF NOT EXISTS episodes_track ON episodes(track_id);
CREATE INDEX IF NOT EXISTS episodes_podcast ON episodes(podcast_id, published);
"""

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
MAX_FEED_BYTES = 20 * 1024 * 1024
MAX_EPISODE_BYTES = 1024 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
USER_AGENT = "RioServerNG/1.0 (podcasts; +https://github.com/msalty/dell-rio-server)"
PLAYED_TAIL_MS = 45_000  # this close to the end counts as finished
SAVE_EVERY_MS = 5_000  # save a position once it has moved this far
DELETE_PLAYED_AFTER = 24 * 3600
DEFAULT_KEEP = 3
MP3_TYPES = {"audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg", "audio/x-mp3"}


class PodcastError(Exception):
    pass


# ---------------------------------------------------------------- feeds

def _text(el, path: str) -> str:
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def plain_text(raw: str, limit: int = 4000) -> str:
    """Show notes without their HTML."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s).strip()
    return s[:limit]


def parse_duration(raw: str) -> int:
    """itunes:duration ("1:02:03", "62:03" or seconds) in milliseconds."""
    raw = (raw or "").strip()
    if not raw:
        return 0
    try:
        parts = [float(p) for p in raw.split(":")]
    except ValueError:
        return 0
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return int(seconds * 1000)


def parse_date(raw: str) -> float:
    try:
        return email.utils.parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0.0


def is_mp3(url: str, mime: str) -> bool:
    mime = (mime or "").split(";")[0].strip().lower()
    if mime in MP3_TYPES:
        return True
    path = urlparse(url).path.lower()
    return path.endswith(".mp3") and (not mime or mime.startswith("audio/") or mime == "application/octet-stream")


def parse_feed(data: bytes) -> dict:
    """An RSS podcast feed: the show and its episodes, newest first."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise PodcastError(f"this isn't a podcast feed ({exc})")
    channel = root.find("channel")
    if channel is None:
        raise PodcastError("this isn't a podcast feed (no RSS channel)")
    image = channel.find(f"{ITUNES}image")
    image_url = image.get("href", "").strip() if image is not None else ""
    image_url = image_url or _text(channel, "image/url")
    show = {
        "title": _text(channel, "title") or "Untitled podcast",
        "author": _text(channel, f"{ITUNES}author") or _text(channel, "managingEditor"),
        "description": plain_text(_text(channel, "description") or _text(channel, f"{ITUNES}summary")),
        "link": _text(channel, "link"),
        "image_url": image_url,
        "episodes": [],
    }
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        url = enc.get("url").strip()
        try:
            size = int(enc.get("length") or 0)
        except ValueError:
            size = 0
        show["episodes"].append({
            "guid": _text(item, "guid") or url,
            "title": _text(item, "title") or "Untitled episode",
            "description": plain_text(_text(item, "description") or _text(item, f"{ITUNES}summary")
                                      or _text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")),
            "url": url,
            "mime": (enc.get("type") or "").strip(),
            "size": max(0, size),
            "published": parse_date(_text(item, "pubDate")),
            "duration_ms": parse_duration(_text(item, f"{ITUNES}duration")),
        })
    show["episodes"].sort(key=lambda e: -e["published"])
    return show


def parse_opml(data: bytes) -> list[str]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise PodcastError(f"this isn't an OPML file ({exc})")
    urls = []
    for el in root.iter("outline"):
        url = (el.get("xmlUrl") or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def make_opml(podcasts: list[dict]) -> str:
    head = ET.Element("opml", version="2.0")
    ET.SubElement(ET.SubElement(head, "head"), "title").text = "RioServer NG podcasts"
    body = ET.SubElement(head, "body")
    for p in podcasts:
        ET.SubElement(body, "outline", type="rss", text=p["title"], title=p["title"], xmlUrl=p["url"])
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(head, encoding="unicode")


# ---------------------------------------------------------------- manager

class Podcasts:
    def __init__(self, library, data_dir: Path, refresh_interval: int = 3 * 3600):
        self.lib = library
        self.dir = Path(data_dir) / "podcasts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.refresh_interval = refresh_interval
        self._saved: dict[int, int] = {}  # track id -> position last written
        self._downloads: asyncio.Queue | None = None
        self._tasks: list[asyncio.Task] = []
        self._session = None
        self._refreshing: set[int] = set()
        with self.lib._conn() as c:
            c.executescript(SCHEMA)
            # A download cut short by a restart is tried again.
            c.execute("UPDATE episodes SET status = 'queued' WHERE status = 'downloading'")

    # -- database helpers -------------------------------------------------
    def _q(self, sql, args=()):
        return self.lib._q(sql, args)

    def _q1(self, sql, args=()):
        return self.lib._q1(sql, args)

    def _exec(self, sql, args=()):
        c = self.lib._conn()
        cur = c.execute(sql, tuple(args))
        c.commit()
        return cur

    # -- reading ----------------------------------------------------------
    def shows(self) -> list[dict]:
        return self._q(
            "SELECT p.id, p.url, p.title, p.author, p.description, p.link, p.keep, p.checked, p.error, "
            "COUNT(e.id) AS episode_count, "
            "SUM(e.status = 'ready') AS downloaded, "
            "SUM(e.status = 'ready' AND e.played = 0) AS unplayed, "
            "SUM(e.status = 'ready' AND e.played = 0 AND e.position_ms > 0) AS in_progress, "
            "MAX(e.published) AS latest "
            "FROM podcasts p LEFT JOIN episodes e ON e.podcast_id = p.id "
            "GROUP BY p.id ORDER BY p.title COLLATE NOCASE")

    def show(self, podcast_id: int) -> dict | None:
        p = self._q1("SELECT id, url, title, author, description, link, keep, checked, error FROM podcasts "
                     "WHERE id = ?", (podcast_id,))
        if p:
            p["episodes"] = self.episodes(podcast_id)
        return p

    def episodes(self, podcast_id: int, ready_only: bool = False, limit: int = 300) -> list[dict]:
        return self._q(
            "SELECT e.id, e.podcast_id, e.title, e.description, e.url, e.size, e.published, "
            "COALESCE(NULLIF(t.duration_ms, 0), e.duration_ms) AS duration_ms, e.status, e.error, "
            "e.track_id, e.pinned, e.position_ms, e.played, e.played_at, e.downloaded_at "
            "FROM episodes e LEFT JOIN tracks t ON t.id = e.track_id WHERE e.podcast_id = ? "
            + ("AND e.status = 'ready' AND e.track_id IS NOT NULL " if ready_only else "")
            + "ORDER BY e.published DESC, e.id DESC LIMIT ?", (podcast_id, limit))

    def episode(self, episode_id: int) -> dict | None:
        return self._q1("SELECT * FROM episodes WHERE id = ?", (episode_id,))

    def by_track(self, track_id: int) -> dict | None:
        return self._q1("SELECT * FROM episodes WHERE track_id = ?", (track_id,))

    def episode_tracks(self, podcast_id: int) -> list[dict]:
        """Downloaded episodes as library tracks (newest first), with where
        they were left off."""
        eps = self.episodes(podcast_id, ready_only=True)
        tracks = {t["id"]: t for t in self.lib.tracks_by_ids([e["track_id"] for e in eps])}
        out = []
        for e in eps:
            t = tracks.get(e["track_id"])
            if t:
                t.update(episode_id=e["id"], position_ms=e["position_ms"], played=e["played"],
                         published=e["published"])
                out.append(t)
        return out

    def recent_episode_tracks(self, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT podcast_id FROM episodes WHERE status = 'ready' GROUP BY podcast_id")
        tracks = [t for r in rows for t in self.episode_tracks(r["podcast_id"])]
        tracks.sort(key=lambda t: -t["published"])
        return tracks[:limit]

    def cover_path(self, podcast_id: int) -> Path | None:
        d = self.dir / str(podcast_id)
        for ext in (".jpg", ".png", ".webp", ".gif"):
            f = d / f"cover{ext}"
            if f.is_file():
                return f
        return None

    # -- positions (any device) -------------------------------------------
    def resume_ms(self, track_id: int) -> int:
        e = self._q1("SELECT position_ms, played FROM episodes WHERE track_id = ?", (track_id,))
        if not e or e["played"]:
            return 0
        pos = e["position_ms"]
        return pos - 2000 if pos > 10_000 else pos  # a couple of seconds back, to pick up the thread

    def progress(self, track_id: int, pos_ms: int, duration_ms: int) -> None:
        """A device is at pos_ms in an episode. Saved every few seconds of
        progress; near the end it counts as played."""
        last = self._saved.get(track_id)
        finished = bool(duration_ms) and pos_ms >= duration_ms - min(PLAYED_TAIL_MS, duration_ms // 10 or 1)
        if finished:
            if last == -1:
                return  # already saved as played
        elif last is not None and last != -1 and abs(pos_ms - last) < SAVE_EVERY_MS:
            return
        self._saved[track_id] = -1 if finished else pos_ms
        if finished:
            self._exec("UPDATE episodes SET position_ms = 0, played = 1, played_at = COALESCE(played_at, ?) "
                       "WHERE track_id = ? AND played = 0", (time.time(), track_id))
        else:
            self._exec("UPDATE episodes SET position_ms = ?, played = 0, played_at = NULL WHERE track_id = ?",
                       (pos_ms, track_id))

    def left(self, track_id: int, pos_ms: int) -> None:
        """A device went on to another track; save where it left this one."""
        e = self._q1("SELECT e.id, t.duration_ms FROM episodes e JOIN tracks t ON t.id = e.track_id "
                     "WHERE e.track_id = ?", (track_id,))
        if e:
            self._saved.pop(track_id, None)
            self.progress(track_id, pos_ms, e["duration_ms"])

    def mark(self, episode_id: int, played: bool | None = None, position_ms: int | None = None) -> None:
        e = self.episode(episode_id)
        if not e:
            raise KeyError(episode_id)
        if played is not None:
            self._exec("UPDATE episodes SET played = ?, played_at = ?, position_ms = 0 WHERE id = ?",
                       (1 if played else 0, time.time() if played else None, episode_id))
        if position_ms is not None:
            self._exec("UPDATE episodes SET position_ms = ?, played = 0, played_at = NULL WHERE id = ?",
                       (max(0, int(position_ms)), episode_id))
        if e["track_id"]:
            self._saved.pop(e["track_id"], None)
        self.lib.bump()

    # -- subscribing -----------------------------------------------------
    async def subscribe(self, url: str, keep: int = DEFAULT_KEEP) -> int:
        url = (url or "").strip()
        if not re.match(r"(?i)^https?://", url):
            raise PodcastError("enter the feed's address, starting with http:// or https://")
        old = self._q1("SELECT id FROM podcasts WHERE url = ?", (url,))
        if old:
            return old["id"]
        data, _, _ = await self._fetch(url, MAX_FEED_BYTES)
        if data is None:
            raise PodcastError("the feed couldn't be fetched")
        feed = parse_feed(data)
        pid = self._exec("INSERT INTO podcasts(url, title, keep, added) VALUES (?, ?, ?, ?)",
                         (url, feed["title"], max(0, min(50, keep)), time.time())).lastrowid
        await self._store_feed(pid, feed)
        log.info("subscribed to %r (%d episodes)", feed["title"], len(feed["episodes"]))
        self.lib.bump()
        return pid

    def unsubscribe(self, podcast_id: int) -> None:
        tracks = [r["track_id"] for r in self._q("SELECT track_id FROM episodes WHERE podcast_id = ? AND "
                                                 "track_id IS NOT NULL", (podcast_id,))]
        self.lib.remove_tracks(tracks)
        self._exec("DELETE FROM episodes WHERE podcast_id = ?", (podcast_id,))
        self._exec("DELETE FROM podcasts WHERE id = ?", (podcast_id,))
        shutil.rmtree(self.dir / str(podcast_id), ignore_errors=True)
        self.lib.bump()

    def set_keep(self, podcast_id: int, keep: int) -> None:
        self._exec("UPDATE podcasts SET keep = ? WHERE id = ?", (max(0, min(50, int(keep))), podcast_id))
        self._plan_downloads(podcast_id)
        self._clean_up(podcast_id)

    async def refresh(self, podcast_id: int) -> None:
        p = self._q1("SELECT * FROM podcasts WHERE id = ?", (podcast_id,))
        if not p or podcast_id in self._refreshing:
            return
        self._refreshing.add(podcast_id)
        try:
            data, etag, modified = await self._fetch(p["url"], MAX_FEED_BYTES, etag=p["etag"],
                                                     modified=p["last_modified"])
            if data is None:  # not modified
                self._exec("UPDATE podcasts SET checked = ?, error = '' WHERE id = ?", (time.time(), podcast_id))
            else:
                feed = parse_feed(data)
                self._exec("UPDATE podcasts SET etag = ?, last_modified = ? WHERE id = ?",
                           (etag or "", modified or "", podcast_id))
                await self._store_feed(podcast_id, feed)
        except Exception as exc:
            log.warning("podcast %r: %s", p["title"], exc)
            self._exec("UPDATE podcasts SET checked = ?, error = ? WHERE id = ?",
                       (time.time(), str(exc)[:300], podcast_id))
        finally:
            self._refreshing.discard(podcast_id)
        self._plan_downloads(podcast_id)
        self._clean_up(podcast_id)

    async def refresh_all(self) -> None:
        for p in self._q("SELECT id FROM podcasts"):
            await self.refresh(p["id"])

    async def _store_feed(self, pid: int, feed: dict) -> None:
        self._exec("UPDATE podcasts SET title = ?, author = ?, description = ?, link = ?, image_url = ?, "
                   "checked = ?, error = '' WHERE id = ?",
                   (feed["title"], feed["author"], feed["description"], feed["link"], feed["image_url"],
                    time.time(), pid))
        c = self.lib._conn()
        new = 0
        for e in feed["episodes"]:
            status = "new" if is_mp3(e["url"], e["mime"]) else "unsupported"
            cur = c.execute(
                "INSERT OR IGNORE INTO episodes(podcast_id, guid, title, description, url, mime, size, published, "
                "duration_ms, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, e["guid"], e["title"], e["description"], e["url"], e["mime"], e["size"], e["published"],
                 e["duration_ms"], status))
            if cur.rowcount:
                new += 1
            else:
                c.execute("UPDATE episodes SET title = ?, description = ?, url = ?, duration_ms = "
                          "CASE WHEN duration_ms = 0 THEN ? ELSE duration_ms END WHERE podcast_id = ? AND guid = ?",
                          (e["title"], e["description"], e["url"], e["duration_ms"], pid, e["guid"]))
        c.commit()
        if new:
            log.info("podcast %r: %d new episodes", feed["title"], new)
        if feed["image_url"] and (self.cover_path(pid) is None or new):
            await self._save_cover(pid, feed["image_url"])
        self._plan_downloads(pid)

    async def _save_cover(self, pid: int, url: str) -> None:
        try:
            data, _, _, ctype = await self._fetch(url, MAX_IMAGE_BYTES, want_type=True)
        except Exception as exc:
            log.info("podcast cover %s: %s", url, exc)
            return
        if not data:
            return
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get((ctype or "").split(";")[0],
                                                                                   ".jpg")
        d = self.dir / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("cover.*"):
            old.unlink(missing_ok=True)
        (d / f"cover{ext}").write_bytes(data)

    # -- downloading -----------------------------------------------------
    def _plan_downloads(self, pid: int) -> None:
        """Queue the newest `keep` episodes that haven't been played."""
        p = self._q1("SELECT keep FROM podcasts WHERE id = ?", (pid,))
        if not p:
            return
        newest = self._q("SELECT id, status, played FROM episodes WHERE podcast_id = ? AND status != 'unsupported' "
                         "ORDER BY published DESC, id DESC LIMIT ?", (pid, p["keep"]))
        for e in newest:
            if e["status"] == "new" and not e["played"]:
                self._queue_download(e["id"])

    def download(self, episode_id: int) -> None:
        """Download an episode on request; it is kept until it is played."""
        e = self.episode(episode_id)
        if not e:
            raise KeyError(episode_id)
        if e["status"] == "unsupported":
            raise PodcastError("this episode isn't an MP3, which is all the receivers can play")
        self._exec("UPDATE episodes SET pinned = 1 WHERE id = ?", (episode_id,))
        if e["status"] in ("new", "failed"):
            self._queue_download(episode_id)

    def _queue_download(self, episode_id: int) -> None:
        self._exec("UPDATE episodes SET status = 'queued', error = '' WHERE id = ? AND status IN ('new', 'failed')",
                   (episode_id,))
        if self._downloads is not None:
            self._downloads.put_nowait(episode_id)

    def delete_download(self, episode_id: int) -> None:
        e = self.episode(episode_id)
        if not e:
            raise KeyError(episode_id)
        self._remove_file(e)
        self._exec("UPDATE episodes SET pinned = 0 WHERE id = ?", (episode_id,))

    def _remove_file(self, e: dict) -> None:
        if e["track_id"]:
            t = self.lib.track(e["track_id"])
            if t:
                Path(t["path"]).unlink(missing_ok=True)
            self.lib.remove_tracks([e["track_id"]])
        self._exec("UPDATE episodes SET status = CASE WHEN status = 'unsupported' THEN status ELSE 'new' END, "
                   "track_id = NULL, downloaded_at = NULL WHERE id = ?", (e["id"],))

    def _clean_up(self, pid: int) -> None:
        p = self._q1("SELECT keep FROM podcasts WHERE id = ?", (pid,))
        if not p:
            return
        newest = {r["id"] for r in self._q("SELECT id FROM episodes WHERE podcast_id = ? AND status != 'unsupported' "
                                           "ORDER BY published DESC, id DESC LIMIT ?", (pid, p["keep"]))}
        cutoff = time.time() - DELETE_PLAYED_AFTER
        for e in self._q("SELECT * FROM episodes WHERE podcast_id = ? AND status = 'ready'", (pid,)):
            finished = e["played"] and (e["played_at"] or 0) < cutoff
            fell_out = e["id"] not in newest and not e["pinned"] and not e["played"] and not e["position_ms"]
            if finished or fell_out:
                log.info("podcast: removing the download of %r (%s)", e["title"],
                         "played" if finished else "no longer among the newest")
                self._remove_file(e)

    async def _download_worker(self) -> None:
        while True:
            episode_id = await self._downloads.get()
            try:
                await self._download(episode_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("podcast download %d failed: %s", episode_id, exc)
                self._exec("UPDATE episodes SET status = 'failed', error = ? WHERE id = ?",
                           (str(exc)[:300], episode_id))

    async def _download(self, episode_id: int) -> None:
        from .library import read_track_metadata

        e = self.episode(episode_id)
        if not e or e["status"] != "queued":
            return
        p = self._q1("SELECT * FROM podcasts WHERE id = ?", (e["podcast_id"],))
        if not p:
            return
        self._exec("UPDATE episodes SET status = 'downloading' WHERE id = ?", (episode_id,))
        d = self.dir / str(p["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{episode_id}-{uuid.uuid4().hex[:8]}.part"
        final = d / f"{episode_id}.mp3"
        try:
            await self._fetch_to_file(e["url"], tmp, MAX_EPISODE_BYTES)
            loop = asyncio.get_running_loop()
            md = await loop.run_in_executor(None, read_track_metadata, tmp, Path(final.name))
            if md is None:
                raise PodcastError("the download isn't an MP3 the receivers can play")
            tmp.replace(final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        md.update(
            title=e["title"], title_sort="", artist=p["author"] or p["title"], album_artist=p["title"],
            album_artist_sort="", album=p["title"], album_sort="", genre="Podcast",
            year=time.gmtime(e["published"]).tm_year if e["published"] else md.get("year"),
            track_no=None, track_total=None, disc_no=None)
        tid = self.lib.add_episode_track(final, md, p["id"])
        self._exec("UPDATE episodes SET status = 'ready', error = '', track_id = ?, downloaded_at = ? WHERE id = ?",
                   (tid, time.time(), episode_id))
        log.info("podcast %r: downloaded %r", p["title"], e["title"])
        self._clean_up(p["id"])
        self.lib.bump()

    # -- HTTP --------------------------------------------------------------
    async def _client(self):
        import aiohttp

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT}, trust_env=True,
                timeout=aiohttp.ClientTimeout(total=None, connect=20, sock_read=60))
        return self._session

    async def _fetch(self, url: str, limit: int, etag: str = "", modified: str = "", want_type: bool = False):
        """(body, etag, last-modified[, content type]); body is None when the
        server says nothing changed."""
        session = await self._client()
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        async with session.get(url, headers=headers) as resp:
            if resp.status == 304:
                result = (None, etag, modified)
            else:
                if resp.status >= 400:
                    raise PodcastError(f"the server answered {resp.status} {resp.reason}")
                data = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    data += chunk
                    if len(data) > limit:
                        raise PodcastError("that is far too big for a feed")
                result = (bytes(data), resp.headers.get("ETag", ""), resp.headers.get("Last-Modified", ""))
            return result + (resp.headers.get("Content-Type", ""),) if want_type else result

    async def _fetch_to_file(self, url: str, path: Path, limit: int) -> None:
        session = await self._client()
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise PodcastError(f"the server answered {resp.status} {resp.reason}")
            got = 0
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(256 * 1024):
                    got += len(chunk)
                    if got > limit:
                        raise PodcastError("the episode is larger than 1 GB")
                    f.write(chunk)

    # -- running -------------------------------------------------------------
    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._downloads = asyncio.Queue()
        for e in self._q("SELECT id FROM episodes WHERE status = 'queued'"):
            self._downloads.put_nowait(e["id"])
        self._tasks.append(loop.create_task(self._download_worker()))

        async def refresher():
            await asyncio.sleep(5)
            while True:
                try:
                    await self.refresh_all()
                except Exception:
                    log.exception("refreshing podcasts failed")
                if self.refresh_interval <= 0:
                    return
                await asyncio.sleep(self.refresh_interval)
        self._tasks.append(loop.create_task(refresher()))

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._session is not None:
            await self._session.close()
