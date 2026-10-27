"""Keeps the library in sync with the music volume without manual rescans.

Two mechanisms work together:
  * filesystem events (inotify via watchdog, or polling for network shares)
    trigger an incremental scan a few seconds after things go quiet, and
  * a periodic incremental scan catches anything the events missed.
"""

from __future__ import annotations

import logging
import threading
import time

from .library import AUDIO_EXTENSIONS, PLAYLIST_EXTENSIONS, Library

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 5.0


class LibraryWatcher:
    def __init__(self, library: Library, interval: int, use_events: bool, polling: bool):
        self.library = library
        self.interval = interval
        self.use_events = use_events
        self.polling = polling
        self._dirty_at: float | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._observer = None
        self._thread = threading.Thread(target=self._run, name="library-watcher", daemon=True)

    def start(self) -> None:
        if self.use_events:
            self._start_observer()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._observer is not None:
            self._observer.stop()

    def request_scan(self, full: bool = False) -> None:
        self._full = full
        self._dirty_at = 0.0
        self._wake.set()

    def _start_observer(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            if self.polling:
                from watchdog.observers.polling import PollingObserver as Observer
            else:
                from watchdog.observers import Observer
        except ImportError:  # pragma: no cover
            log.warning("watchdog not installed; relying on periodic rescans")
            return

        watcher = self
        interesting = AUDIO_EXTENSIONS | PLAYLIST_EXTENSIONS

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.event_type in ("opened", "closed_no_write"):
                    return
                paths = [getattr(event, "src_path", ""), getattr(event, "dest_path", "")]
                if event.is_directory or any(
                    str(p).lower().endswith(tuple(interesting)) for p in paths if p
                ):
                    watcher._mark_dirty()

        try:
            obs = Observer(timeout=10) if self.polling else Observer()
            obs.schedule(Handler(), str(self.library.music_dir), recursive=True)
            obs.daemon = True
            obs.start()
            self._observer = obs
            log.info("watching %s for changes (%s)", self.library.music_dir,
                     "polling" if self.polling else "inotify")
        except Exception as exc:
            log.warning("could not watch %s (%s); relying on periodic rescans",
                        self.library.music_dir, exc)

    def _mark_dirty(self) -> None:
        self._dirty_at = time.monotonic()
        self._wake.set()

    def _run(self) -> None:
        self._full = False
        self.library.scan()
        next_periodic = time.monotonic() + self.interval if self.interval > 0 else None
        while not self._stop.is_set():
            timeout = None
            now = time.monotonic()
            if self._dirty_at is not None:
                timeout = max(0.0, self._dirty_at + DEBOUNCE_SECONDS - now) if self._dirty_at else 0.0
            if next_periodic is not None:
                t2 = max(0.0, next_periodic - now)
                timeout = t2 if timeout is None else min(timeout, t2)
            self._wake.wait(timeout)
            self._wake.clear()
            if self._stop.is_set():
                break
            now = time.monotonic()
            due_event = self._dirty_at is not None and (
                self._dirty_at == 0.0 or now >= self._dirty_at + DEBOUNCE_SECONDS
            )
            due_periodic = next_periodic is not None and now >= next_periodic
            if due_event or due_periodic:
                self._dirty_at = None
                full, self._full = self._full, False
                try:
                    self.library.scan(full=full)
                except Exception:
                    log.exception("library scan failed")
                if self.interval > 0:
                    next_periodic = time.monotonic() + self.interval
