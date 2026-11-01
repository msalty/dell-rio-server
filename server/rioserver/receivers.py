"""Tracks the receivers on the network and relays web-UI commands to them.

"Receivers" are everything the web interface can send music to: Rio
receivers, which keep their own play queue and collect commands when they
poll, and players the server feeds track by track (players.QueuePlayer:
Chromecasts and the like). Rio receivers are known by MAC address (or by
IP until they report one), other players by an id such as "cast-<uuid>".

Every player has a play queue (playqueue.PlayQueue) the web interface can
show and change. A Rio receiver's firmware keeps the real one; the server
mirrors it from the track lists the firmware fetches and the position it
reports, and sends changes back with the "queue" command (firmware 1.6+).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import ids
from .playqueue import PlayQueue

log = logging.getLogger(__name__)

ONLINE_SECONDS = 20  # NG firmware polls every few seconds
# Commands where only the latest one matters: while a slider is dragged, a
# newer value replaces one the receiver hasn't collected yet.
LATEST_WINS = {"vol", "seek"}
QUEUE_FIRMWARE = (1, 6)  # NG firmware that takes queue changes without restarting the track
PUSH_SETTLE = 15.0  # seconds a pushed queue change may take to reach the receiver


class QueueError(Exception):
    """A queue change this player can't make (the message says why)."""


def firmware_version(text) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(text or "").split("."))
    except ValueError:
        return ()


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


@dataclass
class Receiver:
    kind = "rio"

    ip: str
    mac: str | None = None
    firmware: str = "unknown"  # "ng", "stock" or "unknown"
    stage: str = "discovered"  # discovered -> booting -> running
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    state: dict = field(default_factory=dict)
    commands: deque = field(default_factory=lambda: deque(maxlen=32))
    wake_port: int = 0  # UDP port where the firmware listens for "poll now"
    label: str = ""  # name given in the web interface
    sleep_at: float | None = None  # sleep timer: when playback stops
    audio_buffer_ms: int = 0  # how much sound its audio device holds (reported by NG firmware 1.3+)
    cpu_high_polls: int = 0  # status polls in a row with the CPU above CPU_WARN_PERCENT
    cpu_warned_track: int | None = None  # the track it was last warned about
    pq: PlayQueue = field(default_factory=PlayQueue)  # mirror of the firmware's queue
    queue_known: bool = False  # the mirror matches what the receiver plays
    pending_qid: int | None = None  # a queue change sent, not yet fetched by the receiver
    pending_at: float = 0.0
    last_shuf: str | None = None  # shuffle setting at the previous poll

    @property
    def queue_capable(self) -> bool:
        return self.firmware == "ng" and firmware_version(self.state.get("ver")) >= QUEUE_FIRMWARE

    @property
    def id(self) -> str:
        """How the web interface refers to this receiver: its MAC, which stays
        the same when DHCP or a proxy changes the address we see."""
        return self.key

    @property
    def key(self) -> str:
        return self.mac or self.ip

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        return f"Receiver {self.mac[-8:]}" if self.mac else f"Receiver {self.ip}"

    def to_json(self) -> dict:
        online = time.time() - self.last_seen < (ONLINE_SECONDS if self.firmware == "ng" else 300)
        return {
            "id": self.id,
            "kind": self.kind,
            "ip": self.ip,
            "mac": self.mac,
            "name": self.name,
            "label": self.label,
            "firmware": self.firmware,
            "stage": self.stage,
            "online": online,
            "controllable": self.firmware == "ng" and online,
            "queue_editable": self.queue_capable and self.queue_known,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "state": self.state,
            "sleep_at": self.sleep_at,
        }


class ReceiverRegistry:
    def __init__(self, names_path: Path | None = None):
        self._names_path = names_path
        self._names: dict[str, str] = {}
        if names_path:
            try:
                self._names = {str(k): str(v) for k, v in json.loads(names_path.read_text()).items()}
            except (OSError, ValueError, AttributeError):
                pass
        # A receiver can reach us from more than one address: it boots (SSDP,
        # NFS) from its own LAN address, but its HTTP requests may arrive from
        # a proxy in front of the server. Every address maps to the receiver.
        self._by_ip: dict[str, Receiver] = {}
        self._by_mac: dict[str, Receiver] = {}
        self._players: dict[str, object] = {}  # QueuePlayers by id
        self._queues: dict[int, list[int]] = {}
        self._next_queue = 1
        self._sock: socket.socket | None = None
        # Where a player should start a track it starts afresh (podcast
        # resume): (player id, track dict) -> milliseconds. Set by the app.
        self.start_position: Callable[[str, dict], int] | None = None

    def touch(self, ip: str, *, mac: str | None = None, firmware: str | None = None,
              stage: str | None = None) -> Receiver:
        mac = mac.lower() if mac else None
        other = self._by_ip.get(ip)
        r = self._by_mac.get(mac) if mac else None
        if r is None:
            # a different MAC at a known address: DHCP gave it to another unit
            r = other if other and not (mac and other.mac and other.mac != mac) else Receiver(ip)
        if other is not None and other is not r:
            self._release(other, ip)
        self._by_ip[ip] = r
        if mac:
            r.mac = mac
            self._by_mac[mac] = r
        if r.ip != ip and (_is_loopback(r.ip) or not _is_loopback(ip)):
            # Moved to a new address. Keep the LAN address over a proxy's
            # loopback one: it is what we show and where wake-ups must go.
            if not _is_loopback(r.ip) and self._by_ip.get(r.ip) is r:
                del self._by_ip[r.ip]
            r.ip = ip
        r.last_seen = time.time()
        r.label = self._names.get(r.key, "")
        if firmware and (firmware == "ng" or r.firmware == "unknown"):
            r.firmware = firmware
        if stage:
            r.stage = stage
        return r

    def _release(self, r: Receiver, ip: str) -> None:
        """Another receiver has taken over `ip`; forget `r` if that was its
        only address (an entry without a MAC is just this address)."""
        if r.ip != ip:
            return
        rest = [a for a, x in self._by_ip.items() if x is r and a != ip]
        if rest:
            r.ip = rest[0]
        elif r.mac and self._by_mac.get(r.mac) is r:
            del self._by_mac[r.mac]

    def add(self, player) -> None:
        """Add a player the server feeds track by track (a QueuePlayer)."""
        player.label = self._names.get(player.key, "")
        player.start_position = self._start_position
        self._players[player.id] = player

    def _start_position(self, player_id: str, track: dict) -> int:
        return self.start_position(player_id, track) if self.start_position else 0

    def players(self) -> list:
        """The players the server feeds track by track."""
        return list(self._players.values())

    def close(self) -> None:
        for p in self._players.values():
            p.close()

    def rename(self, r: Receiver, name: str) -> None:
        """Name a receiver ("Kitchen"); an empty name restores the default.
        Names are remembered by MAC address, so they survive a new IP."""
        name = " ".join(name.split())[:40]
        if name:
            self._names[r.key] = name
        else:
            self._names.pop(r.key, None)
        r.label = name
        if self._names_path:
            try:
                self._names_path.write_text(json.dumps(self._names, indent=1, sort_keys=True))
            except OSError as exc:
                log.warning("could not save receiver names: %s", exc)

    def command(self, r: Receiver, *args) -> None:
        """Queue a command for an NG receiver and ask it to collect it now,
        or pass it straight to a player the server drives."""
        verb = args[0]
        if not isinstance(r, Receiver):
            r.command(verb, *args[1:2])
            return
        if verb == "shuffle":
            self.edit(r, "shuffle", on=str(args[1] if len(args) > 1 else "") in ("1", "true", "on"))
            return
        if verb in LATEST_WINS:
            for old in [c for c in r.commands if c[0] == verb]:
                r.commands.remove(old)
        r.commands.append(tuple(args))
        if verb == "vol" and len(args) > 1:
            try:
                r.state["vol"] = str(max(0, min(100, int(args[1]))))  # show it right away
            except ValueError:
                pass
        self.wake(r)

    def wake(self, r: Receiver) -> None:
        if not r.wake_port:
            return  # older firmware: the command waits for the next regular poll
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)
        try:
            self._sock.sendto(b"poll", (r.ip, r.wake_port))
        except OSError as exc:
            log.debug("could not wake %s: %s", r.ip, exc)

    def get(self, receiver_id: str):
        """A receiver by id (MAC), by any of its addresses, or a player by id."""
        return (self._by_mac.get(receiver_id.lower()) or self._by_ip.get(receiver_id)
                or self._players.get(receiver_id))

    def all(self) -> list:
        cutoff = time.time() - 7 * 86400
        unique = {id(r): r for r in self._by_ip.values()}.values()  # one receiver, several addresses
        rio = sorted((r for r in unique if r.last_seen > cutoff), key=lambda r: -r.last_seen)
        others = sorted((p for p in self._players.values() if p.online or p.last_seen > cutoff),
                        key=lambda p: (not p.online, p.name.lower()))
        return rio + others

    # Ad-hoc queues let the web UI hand any list of tracks to a receiver.
    def store_queue(self, track_ids: list[int]) -> int:
        qid = self._next_queue
        self._next_queue += 1
        self._queues[qid] = list(track_ids)
        if len(self._queues) > 64:
            del self._queues[min(self._queues)]
        return qid

    def queue(self, qid: int) -> list[int] | None:
        return self._queues.get(qid)

    # -- play queues ----------------------------------------------------
    def play(self, r, tracks: list[dict], index: int = 0, shuffle: bool = False,
             start_ms: int | None = None) -> None:
        """Play a list of tracks (dicts from the library) on any player."""
        if not isinstance(r, Receiver):
            r.play(tracks, index, shuffle, start_ms)
            return
        node = f"q:{self.store_queue([t['id'] for t in tracks])}"
        self.command(r, "play", node, index, 1 if shuffle else 0)
        if start_ms:
            self.command(r, "seek", int(start_ms))

    def edit(self, r, op: str, tracks: list[dict] | None = None, pos: int = 0, to: int = 0,
             on: bool = False) -> None:
        """Change a player's queue (see QueuePlayer.edit for the operations)."""
        if not isinstance(r, Receiver):
            r.edit(op, tracks, pos, to, on)
            return
        if not r.queue_capable:
            raise QueueError("This receiver's firmware can't change its queue. Power-cycle it to load "
                             "the current RioServer NG firmware.")
        if op in ("next", "add") and (not r.queue_known or not r.pq.items or r.state.get("st") == "stop"
                                      and r.pq.index >= len(r.pq) - 1):
            # Nothing (left) to add to: just play them.
            if tracks:
                self.play(r, tracks)
            return
        if not r.queue_known:
            raise QueueError("This receiver is playing a queue the server doesn't know. Play something "
                             "from here to start a new one.")
        start = False
        if op == "next":
            r.pq.insert_next(tracks or [])
        elif op == "add":
            r.pq.append(tracks or [])
        elif op == "move":
            r.pq.move(pos, to)
        elif op == "remove":
            r.pq.remove(pos)
        elif op == "clear":
            r.pq.clear_upcoming()
        elif op == "shuffle":
            r.pq.set_shuffle(on)
        elif op == "jump":
            if not 0 <= pos < len(r.pq):
                return
            r.pq.index = pos
            start = True
        else:
            raise ValueError(f"unknown queue change {op!r}")
        self._push_queue(r, start)

    def _push_queue(self, r: Receiver, start: bool) -> None:
        qid = self.store_queue(r.pq.ids())
        r.pending_qid, r.pending_at = qid, time.monotonic()
        r.last_shuf = "1" if r.pq.shuffle else "0"
        self.command(r, "queue", f"q:{qid}", r.pq.index, 1 if start else 0, 1 if r.pq.shuffle else 0)

    def queue_fetched(self, r: Receiver, node: str, play_ids: list[int], orig_ids: list[int],
                      shuffle: bool) -> None:
        """A receiver fetched a track list to play (from its own menus, or
        because it was told to): that is its queue now."""
        if node == f"q:{r.pending_qid}":
            r.pending_qid = None  # our own change arriving; the mirror has it already
            return
        r.pq.mirror(play_ids, orig_ids, 0, shuffle)
        r.queue_known = True
        r.pending_qid = None
        r.last_shuf = "1" if shuffle else "0"

    def queue_reported(self, r: Receiver, state: dict) -> None:
        """Follow the receiver's position in its queue from a status poll."""
        shuf = state.get("shuf")
        if r.pending_qid is not None and time.monotonic() - r.pending_at < PUSH_SETTLE:
            return  # it hasn't taken our change yet; the old report would look wrong
        r.pending_qid = None
        if not r.queue_known:
            r.last_shuf = shuf
            return
        if shuf in ("0", "1") and r.last_shuf in ("0", "1") and shuf != r.last_shuf:
            # Shuffle was switched on the receiver. Its firmware reordered its
            # copy itself; give it an order the server knows instead.
            r.last_shuf = shuf
            if r.queue_capable:
                r.pq.set_shuffle(shuf == "1")
                self._push_queue(r, False)
            else:
                r.queue_known = False
            return
        r.last_shuf = shuf
        try:
            qi, qn = int(state.get("qi") or 0), int(state.get("qn") or 0)
        except ValueError:
            return
        fid = ids.parse_hex(state.get("id") or "")
        kind = ids.decode_fid(fid) if fid else None
        tid = kind[1] if kind and kind[0] == "track" else None
        if qn == len(r.pq) and 0 <= qi < qn and (tid is None or r.pq.items[qi]["id"] == tid):
            r.pq.index = qi
        elif qn or tid is not None:
            r.queue_known = False  # it plays something else than we think

    def queue_view(self, r) -> dict:
        """What the web interface shows of a player's queue."""
        if isinstance(r, Receiver):
            known = r.queue_known
            return {"known": known, "editable": known and r.queue_capable, "index": r.pq.index,
                    "shuffle": r.pq.shuffle, "track_ids": r.pq.ids() if known else []}
        return {"known": True, "editable": True, "index": r.index, "shuffle": r.shuffle,
                "track_ids": r.pq.ids()}

    def now_playing(self, r) -> tuple[list[int], int, int, bool]:
        """(queue track ids, index, position ms, playing) to hand to another
        player. Only the current track if the rest of the queue isn't known."""
        st = r.state
        try:
            pos = int(st.get("pos") or 0)
        except ValueError:
            pos = 0
        playing = st.get("st") in ("play", "buffer")
        view = self.queue_view(r)
        if view["known"] and view["track_ids"]:
            return view["track_ids"], view["index"], pos, playing
        track = st.get("track")
        return ([track["id"]], 0, pos, playing) if track else ([], 0, 0, False)
