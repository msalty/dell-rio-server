"""Tracks the receivers on the network and relays web-UI commands to them."""

from __future__ import annotations

import ipaddress
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


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


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
    wake_port: int = 0  # UDP port where the firmware listens for "poll now"
    label: str = ""  # name given in the web interface
    sleep_at: float | None = None  # sleep timer: when playback stops

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
        # A receiver can reach us from more than one address: it boots (SSDP,
        # NFS) from its own LAN address, but its HTTP requests may arrive from
        # a proxy in front of the server. Every address maps to the receiver.
        self._by_ip: dict[str, Receiver] = {}
        self._by_mac: dict[str, Receiver] = {}
        self._queues: dict[int, list[int]] = {}
        self._next_queue = 1
        self._sock: socket.socket | None = None

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
        """Queue a command for an NG receiver and ask it to collect it now."""
        verb = args[0]
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

    def get(self, ip: str) -> Receiver | None:
        return self._by_ip.get(ip)

    def all(self) -> list[Receiver]:
        cutoff = time.time() - 7 * 86400
        unique = {id(r): r for r in self._by_ip.values()}.values()
        return sorted((r for r in unique if r.last_seen > cutoff), key=lambda r: -r.last_seen)

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
