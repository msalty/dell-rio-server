"""Small programs the AirPlay and Spotify Connect processes run, which pass
what they say to the server over a Unix datagram socket.

    python -m rioserver.hook event
        librespot's --onevent program: sends its PLAYER_EVENT environment.
    python -m rioserver.hook publish <name> <type> <port> <txt>...
        stands in for avahi-publish-service, which shairport-sync's
        external mDNS backend runs to advertise itself: the server
        advertises the service instead, and withdraws it when this program
        is stopped (SIGTERM) or shairport-sync goes away.

RIO_HOOK_SOCKET (the server's socket) and RIO_HOOK_INSTANCE (which process
this is for) come from the environment the server starts those processes in.
Only the standard library is used, so it starts quickly.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time

# librespot's event variables worth passing on (see its player_event_handler.rs)
EVENT_KEYS = ("PLAYER_EVENT", "TRACK_ID", "NAME", "ARTISTS", "ALBUM", "COVERS", "DURATION_MS", "POSITION_MS",
              "VOLUME", "CLIENT_NAME", "CLIENT_BRAND_NAME", "CLIENT_MODEL_NAME", "USER_NAME", "SINK_STATUS",
              "ITEM_TYPE", "SHOW_NAME")


def send(message: dict) -> None:
    path = os.environ.get("RIO_HOOK_SOCKET")
    if not path:
        return
    message["instance"] = os.environ.get("RIO_HOOK_INSTANCE", "")
    data = json.dumps(message).encode()[:60000]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        try:
            s.sendto(data, path)
        except OSError:
            pass  # the server is gone or restarting


def event() -> None:
    send({"kind": "event", "env": {k: os.environ[k] for k in EVENT_KEYS if k in os.environ}})


def publish(args: list[str]) -> None:
    if len(args) < 3:
        sys.exit("usage: avahi-publish-service <name> <type> <port> [txt...]")
    name, regtype, port, txt = args[0], args[1], args[2], args[3:]
    msg = {"kind": "publish", "name": name, "type": regtype, "port": int(port), "txt": txt}
    stopping = []
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stopping.append(1))
    send(msg)
    parent = os.getppid()
    while not stopping and os.getppid() == parent:
        time.sleep(0.5)
    send({"kind": "withdraw", "name": name, "type": regtype})


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "event":
        event()
    elif mode == "publish":
        publish(sys.argv[2:])
    else:
        sys.exit("usage: python -m rioserver.hook event|publish ...")


if __name__ == "__main__":
    main()
