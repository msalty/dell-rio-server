"""The live MP3 stream: PCM in at real time, MP3 out to several listeners."""

import array
import asyncio
import math
import shutil
import subprocess
import time

import pytest

from rioserver import live
from rioserver.live import BYTES_PER_SECOND, LiveStream

pytestmark = pytest.mark.skipif(not live.find_encoder(), reason="needs lame or ffmpeg")


def tone(seconds: float, freq: float = 440.0, start: int = 0) -> bytes:
    n = int(seconds * live.RATE)
    a = array.array("h")
    for i in range(start, start + n):
        v = int(8000 * math.sin(2 * math.pi * freq * i / live.RATE))
        a.extend((v, v))
    return a.tobytes()


def decoded_seconds(mp3: bytes) -> tuple[float, int]:
    """How long `mp3` plays, and its loudest sample."""
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg to check the MP3")
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0", "-f", "s16le", "-ac", "2",
                          "-ar", "44100", "pipe:1"], input=mp3, capture_output=True, check=True).stdout
    a = array.array("h", pcm)
    return len(a) / 2 / live.RATE, max((abs(x) for x in a), default=0)


async def feeder(stream: LiveStream, seconds: float):
    """A source writing in real time, in small packets as shairport-sync does."""
    sent = 0
    began = time.monotonic()
    while sent < seconds * live.RATE:
        stream.feed(tone(0.02, start=sent))
        sent += int(0.02 * live.RATE)
        await asyncio.sleep(max(0.0, sent / live.RATE - (time.monotonic() - began)))


async def collect(lst, seconds: float) -> bytes:
    out = bytearray()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            chunk = await asyncio.wait_for(lst.get(), max(0.01, end - time.monotonic()))
        except asyncio.TimeoutError:
            break
        if chunk is None:
            break
        out += chunk
    return bytes(out)


async def test_tone_reaches_every_listener_as_mp3():
    st = LiveStream("test", bitrate=160)
    await st.start()
    try:
        a, b = st.listen(), st.listen()
        feed = asyncio.create_task(feeder(st, 3.0))
        got_a, got_b = await asyncio.gather(collect(a, 3.0), collect(b, 3.0))
        feed.cancel()
        for got in (got_a, got_b):
            seconds, peak = decoded_seconds(got)
            assert 2.0 < seconds < 3.6, seconds
            assert peak > 4000  # the tone, not silence
            # constant bitrate: about 20 KB a second at 160 kbps
            assert 0.8 * 20000 * seconds < len(got) < 1.3 * 20000 * seconds
    finally:
        await st.stop()


async def test_a_slow_listener_is_dropped_without_holding_up_the_others(monkeypatch):
    monkeypatch.setattr(live, "LISTENER_LIMIT", 1)  # seconds behind
    st = LiveStream("test", bitrate=160)
    await st.start()
    try:
        stuck = st.listen()  # never reads
        reader = st.listen()
        feed = asyncio.create_task(feeder(st, 3.5))
        got = await collect(reader, 3.5)
        feed.cancel()
        assert stuck.dropped and st.listeners == 1
        assert decoded_seconds(got)[0] > 2.5  # the other one kept going
    finally:
        await st.stop()


async def test_pcm_written_ahead_is_played_at_real_time():
    """librespot's pipe backend writes as fast as it can: the stream takes
    it at the rate it plays, and the source waits (room())."""
    st = LiveStream("test", bitrate=160)
    await st.start()
    try:
        st.feed(tone(2.0))
        assert st.room() == 0  # a source should wait now
        lst = st.listen()
        got = await collect(lst, 1.5)
        seconds = decoded_seconds(got)[0]
        assert 0.8 < seconds < 2.1, seconds  # not all of it at once
    finally:
        await st.stop()


async def test_silence_fills_in_while_the_source_is_quiet():
    st = LiveStream("test", bitrate=160)
    await st.start()
    try:
        lst = st.listen()
        got = await collect(lst, 2.0)  # nothing fed at all
        seconds, peak = decoded_seconds(got)
        assert seconds > 1.0 and peak < 50
    finally:
        await st.stop()


async def test_encoder_runs_only_during_a_session():
    st = LiveStream("test", bitrate=160)
    st.feed(tone(0.5))  # before start: thrown away
    assert not st.running and st._proc is None
    await st.start()
    proc = st._proc
    assert proc.returncode is None
    lst = st.listen()
    await st.stop()
    assert proc.returncode is not None and not st.running
    assert await lst.get() is None  # listeners are told the stream ended
    await st.start()  # and it can start again
    assert st.running
    await st.stop()


async def test_a_new_listener_starts_with_the_last_moment_of_the_stream():
    st = LiveStream("test", bitrate=160)
    await st.start()
    try:
        feed = asyncio.create_task(feeder(st, 3.0))
        await asyncio.sleep(1.5)
        lst = st.listen()
        first = await asyncio.wait_for(lst.get(), 1)
        assert len(first) > 5000  # ~0.75 s at once, so the receiver starts playing quickly
        assert first[0] == 0xFF and first[1] & 0xE0 == 0xE0  # from a frame header
        feed.cancel()
    finally:
        await st.stop()


def test_metadata_changes_are_reported_once():
    st = LiveStream("test")
    seen = []
    st.on_change.append(seen.append)
    st.set_meta(title="Song", artist="Artist")
    st.set_meta(title="Song")  # no change
    st.set_meta(volume=40)
    assert seen == [{"title": "Song", "artist": "Artist"}, {"volume": 40}]
    assert st.meta == {"title": "Song", "artist": "Artist", "volume": 40}
