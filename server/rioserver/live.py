"""Live audio as an endless MP3 stream, for receivers that only play MP3.

A LiveStream takes raw PCM (s16le, 44.1 kHz, stereo) from a source such as
shairport-sync (AirPlay) or librespot (Spotify Connect), encodes it to
constant-bitrate MP3 with LAME (or ffmpeg) and serves it to any number of
HTTP listeners. Internet radio can reuse it later.

- Pacing. Some sources (librespot's pipe backend) write as fast as they are
  read, others (shairport-sync) in real time. The stream takes PCM at real
  time, whatever the source does, and fills in silence while the source has
  nothing (paused), so listeners get a steady stream and their buffers stay
  at the same level.
- Listeners. Each has its own bounded buffer. One that falls too far behind
  is dropped, so it never holds up the encoder or anyone else. A new
  listener starts with the last moment of the stream, so it can start
  playing at once.
- The encoder only runs between start() and stop(), i.e. while a session
  is on.
- Metadata (title, artist, album, cover art, the sender's name and volume)
  travels with the stream; on_change callbacks hear about every change.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import Callable

from aiohttp import web

log = logging.getLogger(__name__)

RATE = 44100
FRAME_BYTES = 4  # s16le stereo
BYTES_PER_SECOND = RATE * FRAME_BYTES
TICK = 0.04  # seconds between pacing steps
SOURCE_BUFFER = 2 * BYTES_PER_SECOND  # PCM held from a source that writes ahead of time
IDLE_AFTER = 0.5  # seconds without PCM before silence fills in
MAX_BEHIND = 0.5  # seconds a slow source may fall behind before the clock is reset
DEFAULT_BITRATE = 160  # kbps; the receiver's 74 MHz CPU struggles at 320 (firmware/README.md)
LISTENER_LIMIT = 15  # seconds of MP3 a listener may fall behind before it is dropped
BACKLOG = 0.75  # seconds of MP3 a new listener starts with


def find_encoder() -> str | None:
    """'lame' or 'ffmpeg', whichever is installed (the image has lame)."""
    for name in ("lame", "ffmpeg"):
        if shutil.which(name):
            return name
    return None


def encoder_command(encoder: str, bitrate: int) -> list[str]:
    if encoder == "lame":
        return ["lame", "--quiet", "-r", "-s", "44.1", "--bitwidth", "16", "--signed", "--little-endian",
                "-m", "j", "--cbr", "-b", str(bitrate), "-q", "5", "--flush", "-", "-"]
    return ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", "pipe:0",
            "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", "-f", "mp3", "-id3v2_version", "0", "-write_xing", "0",
            "-flush_packets", "1", "pipe:1"]


def _frame_start(data: bytes) -> int:
    """Where the first MP3 frame header in `data` begins (0 if none is found)."""
    i = data.find(b"\xff")
    while 0 <= i < len(data) - 1:
        if data[i + 1] & 0xE0 == 0xE0:
            return i
        i = data.find(b"\xff", i + 1)
    return 0


class Listener:
    def __init__(self, limit: int):
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.pending = 0
        self.limit = limit
        self.dropped = False

    def put(self, chunk: bytes) -> bool:
        if self.dropped:
            return False
        if self.pending + len(chunk) > self.limit:
            self.dropped = True
            self.queue.put_nowait(None)
            return False
        self.pending += len(chunk)
        self.queue.put_nowait(chunk)
        return True

    def close(self) -> None:
        self.queue.put_nowait(None)

    async def get(self) -> bytes | None:
        chunk = await self.queue.get()
        if chunk is not None:
            self.pending -= len(chunk)
        return chunk


class LiveStream:
    def __init__(self, name: str, bitrate: int = DEFAULT_BITRATE, encoder: str | None = None):
        self.name = name
        self.bitrate = bitrate
        self.encoder = encoder or find_encoder()
        self.meta: dict = {}
        self.on_change: list[Callable[[dict], None]] = []
        self._listeners: set[Listener] = set()
        self._backlog = bytearray()
        self._pcm = bytearray()  # from the source, not yet passed on
        self._last_pcm = float("-inf")
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._source: asyncio.StreamReader | None = None
        self.running = False
        self.bytes_out = 0  # MP3 bytes produced since start

    # -- the session -------------------------------------------------------
    async def start(self) -> None:
        if self.running:
            return
        if not self.encoder:
            raise RuntimeError("no MP3 encoder (lame or ffmpeg) installed")
        self._proc = await asyncio.create_subprocess_exec(
            *encoder_command(self.encoder, self.bitrate), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self.running = True
        self._pcm.clear()
        self._backlog.clear()
        self._last_pcm = float("-inf")
        self.bytes_out = 0
        self._tasks = [asyncio.create_task(self._pace()), asyncio.create_task(self._fan_out())]
        log.info("%s: live stream started (%s, %d kbps)", self.name, self.encoder, self.bitrate)

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        for lst in list(self._listeners):
            lst.close()
        self._listeners.clear()
        self._pcm.clear()
        log.info("%s: live stream stopped", self.name)

    # -- PCM in ------------------------------------------------------------
    def room(self) -> int:
        """How much PCM the stream takes right now (it holds a little ahead)."""
        return max(0, SOURCE_BUFFER - len(self._pcm))

    def feed(self, pcm: bytes) -> None:
        """PCM from the source. While no session is on, it is thrown away."""
        if not self.running or not pcm:
            return
        self._pcm += pcm
        if len(self._pcm) > SOURCE_BUFFER * 2:  # a source that ignores room()
            del self._pcm[:len(self._pcm) - SOURCE_BUFFER * 2]
        self._last_pcm = time.monotonic()

    async def read_from(self, reader: asyncio.StreamReader) -> None:
        """Feed PCM from a source process's stdout until it ends. The source
        is read only as fast as the stream plays (it waits otherwise), and
        what it writes outside a session is discarded."""
        while True:
            if self.running and self.room() < 16384:
                await asyncio.sleep(TICK)
                continue
            chunk = await reader.read(16384)
            if not chunk:
                return
            self.feed(chunk)

    async def _pace(self) -> None:
        """Hand the encoder PCM at real time: the source's, or silence."""
        began = time.monotonic()
        sent = 0
        while True:
            await asyncio.sleep(TICK)
            now = time.monotonic()
            due = int((now - began) * BYTES_PER_SECOND) // FRAME_BYTES * FRAME_BYTES - sent
            if due <= 0:
                continue
            if len(self._pcm) >= due:
                out = bytes(self._pcm[:due])
                del self._pcm[:due]
            elif self._pcm:
                out = bytes(self._pcm[:len(self._pcm) // FRAME_BYTES * FRAME_BYTES])
                del self._pcm[:len(out)]
            elif now - self._last_pcm > IDLE_AFTER:
                out = bytes(due)  # silence while the source is quiet
            else:
                out = b""
            sent += len(out)
            if due - len(out) > MAX_BEHIND * BYTES_PER_SECOND:
                sent = due + sent - len(out)  # the source fell behind: don't burst to catch up
            if out:
                try:
                    self._proc.stdin.write(out)
                    await self._proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError, AttributeError):
                    log.warning("%s: the MP3 encoder stopped", self.name)
                    return

    # -- MP3 out -----------------------------------------------------------
    async def _fan_out(self) -> None:
        keep = int(BACKLOG * self.bitrate * 125)
        while True:
            chunk = await self._proc.stdout.read(8192)
            if not chunk:
                return
            self.bytes_out += len(chunk)
            self._backlog += chunk
            if len(self._backlog) > keep:
                del self._backlog[:len(self._backlog) - keep]
            for lst in list(self._listeners):
                if not lst.put(chunk):
                    self._listeners.discard(lst)
                    log.info("%s: a listener fell %d s behind and was dropped", self.name, LISTENER_LIMIT)

    def listen(self) -> Listener:
        lst = Listener(LISTENER_LIMIT * self.bitrate * 125)
        if self._backlog:
            start = _frame_start(bytes(self._backlog[:4096]))
            lst.put(bytes(self._backlog[start:]))
        self._listeners.add(lst)
        return lst

    def unlisten(self, lst: Listener) -> None:
        self._listeners.discard(lst)

    @property
    def listeners(self) -> int:
        return len(self._listeners)

    async def serve(self, req: web.Request) -> web.StreamResponse:
        """The endless HTTP response (plain body, the connection closes at the end)."""
        if not self.running:
            raise web.HTTPNotFound(text="nothing is streaming")
        resp = web.StreamResponse(headers={"Content-Type": "audio/mpeg", "Cache-Control": "no-cache",
                                           "icy-br": str(self.bitrate)})
        resp.force_close()
        await resp.prepare(req)
        lst = self.listen()
        try:
            while True:
                chunk = await lst.get()
                if chunk is None:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.unlisten(lst)
        return resp

    # -- metadata ----------------------------------------------------------
    def set_meta(self, **changes) -> None:
        """Change now-playing details: title, artist, album, art (bytes),
        art_type, art_url, sender, volume (0-100), paused. Callbacks run
        only when something changed."""
        changed = {k: v for k, v in changes.items() if self.meta.get(k) != v}
        if not changed:
            return
        self.meta.update(changed)
        for cb in list(self.on_change):
            try:
                cb(changed)
            except Exception:
                log.exception("live metadata callback")

    def clear_meta(self) -> None:
        self.meta = {}
