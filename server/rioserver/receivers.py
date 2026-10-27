"""Tracks the receivers on the network and relays web-UI commands to them."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

ONLINE_SECONDS = 20  # NG firmware polls every few seconds


@dataclass
class Receiver:
    ip: str
    mac: str | None = None
    firmware: str = "unknown"  # "ng", "stock" or "unknown"
    stage: str = "discovered"  # discovered -> booting -> running
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    state: dict = field(default_factory=dict)
    commands: deque = field(default_factory=lambda: deque(maxlen=32))

    @property
    def name(self) -> str:
        return f"Receiver {self.mac[-8:]}" if self.mac else f"Receiver {self.ip}"

    def to_json(self) -> dict:
        online = time.time() - self.last_seen < (ONLINE_SECONDS if self.firmware == "ng" else 300)
        return {
            "ip": self.ip,
            "mac": self.mac,
            "name": self.name,
            "firmware": self.firmware,
            "stage": self.stage,
            "online": online,
            "controllable": self.firmware == "ng" and online,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "state": self.state,
        }


class ReceiverRegistry:
    def __init__(self):
        self._by_ip: dict[str, Receiver] = {}
        self._queues: dict[int, list[int]] = {}
        self._next_queue = 1

    def touch(self, ip: str, *, mac: str | None = None, firmware: str | None = None,
              stage: str | None = None) -> Receiver:
        r = self._by_ip.get(ip)
        if r is None:
            r = self._by_ip[ip] = Receiver(ip)
        r.last_seen = time.time()
        if mac:
            r.mac = mac.lower()
        if firmware and (firmware == "ng" or r.firmware == "unknown"):
            r.firmware = firmware
        if stage:
            r.stage = stage
        return r

    def get(self, ip: str) -> Receiver | None:
        return self._by_ip.get(ip)

    def all(self) -> list[Receiver]:
        cutoff = time.time() - 7 * 86400
        return sorted((r for r in self._by_ip.values() if r.last_seen > cutoff),
                      key=lambda r: -r.last_seen)

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
