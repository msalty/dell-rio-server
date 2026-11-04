"""The server's one mDNS responder (python-zeroconf), shared by Chromecast
discovery and the AirPlay speakers it advertises.

The container uses host networking, so whatever it announces speaks for the
whole host. Services are published under a host name of our own
(rioserver.local) with only the LAN address, never under the system's host
name: a second responder answering for that name hands out addresses such
as Docker bridges, and its goodbye packets when it stops would briefly
remove the host's real address from other devices' caches.
"""

from __future__ import annotations

import logging
import socket
import threading

log = logging.getLogger(__name__)

HOST_NAME = "rioserver.local."


class SharedZeroconf:
    def __init__(self):
        self._zc = None
        self._lock = threading.Lock()

    def get(self):
        """The Zeroconf instance, started on first use. Zeroconf created on
        the event loop's thread would run on that loop and refuse the
        blocking calls pychromecast makes; created on another thread, it runs
        its own loop on its own thread."""
        with self._lock:
            if self._zc is None:
                import zeroconf

                made: list = []
                t = threading.Thread(target=lambda: made.append(zeroconf.Zeroconf()), daemon=True)
                t.start()
                t.join()
                if not made:
                    raise OSError("zeroconf failed to start")
                self._zc = made[0]
            return self._zc

    @property
    def started(self) -> bool:
        return self._zc is not None


def service_info(regtype: str, name: str, port: int, txt: list[str], ip: str):
    """A ServiceInfo for `name` (e.g. "3E15...@Kitchen") of `regtype`
    ("_raop._tcp"), at `ip` under our own host name."""
    import zeroconf

    full_type = regtype.rstrip(".") + ".local."
    props = {}
    for item in txt:
        key, sep, value = item.partition("=")
        props[key] = value if sep else None
    return zeroconf.ServiceInfo(full_type, f"{name}.{full_type}", port=port, properties=props,
                                server=HOST_NAME, addresses=[socket.inet_aton(ip)])
