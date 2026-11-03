"""A play queue: the tracks a player works through, in order, and where it is.

The server keeps one for every player the web interface can see, so the queue
can be shown, added to and rearranged whichever device is playing it:

- players the server feeds track by track (Chromecasts) play straight from it;
- Rio receivers keep their own copy in the firmware. The server's copy
  mirrors it (see receivers.py) and changes are sent back to the receiver.

Entries are dicts with at least an "id" (a track id). The same track can be
in the queue twice, so entries are told apart by identity, not by id: each
one is a copy made when it was added.
"""

from __future__ import annotations

import random

MAX_LENGTH = 10000


def play_order(count: int, index: int, shuffle: bool) -> tuple[list[int], int]:
    """Positions in the order to play them, and where to start. Shuffling
    puts the chosen track first, as the receiver firmware does."""
    order = list(range(count))
    if not shuffle:
        return order, max(0, min(index, count - 1))
    head = [order.pop(index)] if 0 <= index < count else []
    random.shuffle(order)
    return head + order, 0


def _entries(tracks) -> list[dict]:
    return [dict(t) if isinstance(t, dict) else {"id": int(t)} for t in tracks]


class PlayQueue:
    def __init__(self) -> None:
        self.items: list[dict] = []  # in play order
        self.orig: list[dict] = []  # the same entries in the order given, for un-shuffling
        self.index = 0
        self.shuffle = False

    # -- reading ----------------------------------------------------------
    def __len__(self) -> int:
        return len(self.items)

    @property
    def current(self) -> dict | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def ids(self) -> list[int]:
        return [e["id"] for e in self.items]

    def _orig_pos(self, entry: dict) -> int:
        return next((i for i, e in enumerate(self.orig) if e is entry), -1)

    # -- changing ---------------------------------------------------------
    def load(self, tracks, index: int = 0, shuffle: bool = False) -> int:
        """Replace the queue. Returns the position to start playing at."""
        given = _entries(tracks)[:MAX_LENGTH]
        order, start = play_order(len(given), index, shuffle)
        self.orig = given
        self.items = [given[i] for i in order]
        self.index = start if given else 0
        self.shuffle = bool(shuffle)
        return self.index

    def mirror(self, play_ids: list[int], orig_ids: list[int] | None, index: int, shuffle: bool) -> None:
        """Take on a queue a receiver chose itself (the firmware fetched it)."""
        self.items = _entries(play_ids)[:MAX_LENGTH]
        if orig_ids is None or sorted(orig_ids) != sorted(play_ids):
            self.orig = list(self.items)
        else:
            # Match each original position with an entry of the play order.
            pool: dict[int, list[dict]] = {}
            for e in self.items:
                pool.setdefault(e["id"], []).append(e)
            self.orig = [pool[i].pop(0) for i in orig_ids]
        self.index = max(0, min(index, len(self.items) - 1)) if self.items else 0
        self.shuffle = bool(shuffle)

    def insert_next(self, tracks) -> None:
        new = _entries(tracks)
        if not self.items:
            self.load(new)
            return
        room = MAX_LENGTH - len(self.items)
        new = new[:max(0, room)]
        at = self.index + 1
        self.items[at:at] = new
        cur = self.current
        o = self._orig_pos(cur) + 1 if cur is not None else len(self.orig)
        self.orig[o:o] = new

    def append(self, tracks) -> None:
        new = _entries(tracks)
        if not self.items:
            self.load(new)
            return
        new = new[:max(0, MAX_LENGTH - len(self.items))]
        self.items.extend(new)
        self.orig.extend(new)

    def move(self, src: int, dst: int) -> None:
        n = len(self.items)
        if not (0 <= src < n and 0 <= dst < n) or src == dst:
            return
        entry = self.items.pop(src)
        self.items.insert(dst, entry)
        if src == self.index:
            self.index = dst
        elif src < self.index <= dst:
            self.index -= 1
        elif dst <= self.index < src:
            self.index += 1
        if not self.shuffle:
            self.orig = list(self.items)

    def remove(self, pos: int) -> None:
        if not 0 <= pos < len(self.items):
            return
        if pos == self.index:
            raise ValueError("the track that is playing can't be removed; skip it instead")
        entry = self.items.pop(pos)
        if pos < self.index:
            self.index -= 1
        o = self._orig_pos(entry)
        if o >= 0:
            del self.orig[o]

    def clear_upcoming(self) -> None:
        for entry in self.items[self.index + 1:]:
            o = self._orig_pos(entry)
            if o >= 0:
                del self.orig[o]
        del self.items[self.index + 1:]

    def set_shuffle(self, on: bool) -> None:
        """Shuffle what's still to come (keeping the current track), or go
        back to the original order at the current track."""
        on = bool(on)
        if on == self.shuffle:
            return
        self.shuffle = on
        if not self.items:
            return
        if on:
            rest = self.items[self.index + 1:]
            random.shuffle(rest)
            self.items[self.index + 1:] = rest
        else:
            cur = self.current
            self.items = list(self.orig)
            pos = self._orig_pos(cur)
            self.index = pos if pos >= 0 else 0
