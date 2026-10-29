"""Tracks the receivers on the network and relays web-UI commands to them.

"Receivers" are everything the web interface can send music to: Rio
receivers, which keep their own play queue and collect commands when they
poll, and players the server feeds track by track (players.QueuePlayer:
Chromecasts and the like). Rio receivers are known by IP address, other
players by an id such as "cast-<uuid>".
"""

from __future__ import annotations

import json
import logging
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

ONLINE_SECONDS = 20  # NG firmware polls every few seconds
# Commands where only the latest one matters: while a slider is dragged, a
# newer value replaces one the receiver hasn't collected yet.
LATEST_WINS = {"vol", "seek"}


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

    @property
    def id(self) -> str:
        return self.ip

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
            "id": self.ip,
            "kind": self.kind,
            "ip": self.ip,
            "mac": self.mac,
            "name": self.name,
            "label": self.label,
            "firmware": self.firmware,
            "stage": self.stage,
            "online": online,
            "controllable": self.firmware == "ng" and online,
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
        self._by_ip: dict[str, Receiver] = {}
        self._players: dict[str, object] = {}  # QueuePlayers by id
        self._queues: dict[int, list[int]] = {}
        self._next_queue = 1
        self._sock: socket.socket | None = None

    def touch(self, ip: str, *, mac: str | None = None, firmware: str | None = None,
              stage: str | None = None) -> Receiver:
        r = self._by_ip.get(ip)
        if r is None:
            r = self._by_ip[ip] = Receiver(ip)
        r.last_seen = time.time()
        if mac:
            r.mac = mac.lower()
        r.label = self._names.get(r.key, "")
        if firmware and (firmware == "ng" or r.firmware == "unknown"):
            r.firmware = firmware
        if stage:
            r.stage = stage
        return r

    def add(self, player) -> None:
        """Add a player the server feeds track by track (a QueuePlayer)."""
        player.label = self._names.get(player.key, "")
        self._players[player.id] = player

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
        return self._by_ip.get(receiver_id) or self._players.get(receiver_id)

    def all(self) -> list:
        cutoff = time.time() - 7 * 86400
        rio = sorted((r for r in self._by_ip.values() if r.last_seen > cutoff), key=lambda r: -r.last_seen)
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
