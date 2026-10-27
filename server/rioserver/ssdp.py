"""The Rio Receiver's (pre-UPnP) SSDP discovery.

Receivers broadcast a short datagram to UDP port 21075 and expect a single
line back naming the server:

  * "upnp:uuid:1D274DB1-F053-11d3-BF72-0050DA689B2F<mac>" is sent by the
    kernel while it looks for an NFS server to boot from. The reply is
    "http://<server-ip>/descriptor.xml"; the kernel then asks the portmapper
    on that host where mountd and nfsd are.
  * "upnp:uuid:1D274DB0-F053-11d3-BF72-0050DA689B2F" is sent by the player
    looking for the music server. The reply is
    "http://<server-ip>:<port>/descriptor.xml".

Dell-branded receivers send a slightly different tail on the UUID, so only
the distinctive prefix is matched (JReceiver did the same).
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from typing import Callable

log = logging.getLogger(__name__)

NFS_PREFIX = b"upnp:uuid:1D274DB1"
PLAYER_PREFIX = b"upnp:uuid:1D274DB0"
_MAC_RE = re.compile(rb"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")


def local_ip_for(peer: str) -> str:
    """The local address the kernel would use to reach `peer`."""
    target = peer
    if not peer or peer.startswith("0.") or peer == "255.255.255.255":
        target = "10.255.255.255"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class SSDPResponder(asyncio.DatagramProtocol):
    def __init__(self, server_ip: str, http_port: int,
                 on_discovery: Callable[[str, str, str | None], None] | None = None):
        self.server_ip = server_ip
        self.http_port = http_port
        self.on_discovery = on_discovery
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        sock = transport.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def reply_for(self, data: bytes, peer: str) -> tuple[str, bytes] | None:
        ip = self.server_ip or local_ip_for(peer)
        if data.startswith(NFS_PREFIX):
            return "boot", f"http://{ip}/descriptor.xml\r\n".encode()
        if data.startswith(PLAYER_PREFIX):
            return "player", f"http://{ip}:{self.http_port}/descriptor.xml\r\n".encode()
        return None

    def datagram_received(self, data: bytes, addr):
        res = self.reply_for(data, addr[0])
        if res is None:
            if not data.startswith(b"http://"):  # ignore our own replies
                log.debug("ignoring SSDP datagram from %s: %r", addr[0], data[:64])
            return
        kind, reply = res
        m = _MAC_RE.search(data)
        mac = m.group(1).decode().lower() if m else None
        log.info("SSDP %s request from %s%s -> %s", kind, addr[0],
                 f" ({mac})" if mac else "", reply.decode().strip())
        dest = addr
        if addr[0].startswith("0.") or addr[0] == "255.255.255.255":
            dest = ("255.255.255.255", addr[1])
        self.transport.sendto(reply, dest)
        if self.on_discovery:
            self.on_discovery(addr[0], kind, mac)


async def serve_ssdp(port: int, server_ip: str, http_port: int,
                     on_discovery=None) -> asyncio.DatagramTransport:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", port))
    transport, _ = await loop.create_datagram_endpoint(
        lambda: SSDPResponder(server_ip, http_port, on_discovery), sock=sock
    )
    return transport
