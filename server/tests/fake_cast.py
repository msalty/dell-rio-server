"""A pretend Chromecast for the tests.

It speaks enough of the Cast v2 protocol (TLS, length-prefixed protobuf
messages carrying JSON) for pychromecast to connect, launch the Default
Media Receiver and load, pause, seek and stop media. Loaded URLs are fetched
like a real device would, so the tests see whether the server handed out a
working stream URL. The test drives the "end of track" and "another app took
over" events itself.
"""

from __future__ import annotations

import json
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import urllib.request
import uuid as uuidlib
from pathlib import Path

from pychromecast.generated.cast_channel_pb2 import CastMessage

NS_CONNECTION = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"
APP_NAMES = {"CC1AD845": "Default Media Receiver"}


def make_cert(directory: Path) -> tuple[Path, Path]:
    if not shutil.which("openssl"):
        import pytest
        pytest.skip("openssl is needed to fake a Chromecast")
    cert, key = directory / "cast.crt", directory / "cast.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=fake-cast", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    return cert, key


class FakeChromecast:
    def __init__(self, cert_dir: Path, name: str = "Kitchen speaker"):
        cert, key = make_cert(cert_dir)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.name = name
        self.uuid = uuidlib.uuid4()
        self.volume = 0.5
        self.app: str | None = None
        self.app_name = ""
        self.session = 0
        self.media: dict | None = None
        self.player_state = "IDLE"
        self.idle_reason: str | None = None
        self.current_time = 0.0
        self.received: list[dict] = []  # every non-heartbeat payload, in order
        self.fetched: list[tuple[str, int, str]] = []  # (url, status, content type)
        self.lock = threading.Lock()
        self._conn = None
        self._client = "sender-0"
        self._send_lock = threading.Lock()
        threading.Thread(target=self._serve, daemon=True).start()

    def cast_info(self):
        from pychromecast.models import CastInfo, HostServiceInfo
        return CastInfo({HostServiceInfo("127.0.0.1", self.port)}, self.uuid, "Google Nest Mini",
                        self.name, "127.0.0.1", self.port, "audio", "Google Inc.")

    def close(self) -> None:
        self.listener.close()
        if self._conn:
            try:
                self._conn.close()
            except OSError:
                pass

    def commands(self, ns: str = NS_MEDIA) -> list[str]:
        return [p["type"] for p in self.received if p.get("_ns") == ns]

    # -- things the test makes happen ------------------------------------
    def finish_track(self) -> None:
        self.player_state, self.idle_reason, self.current_time = "IDLE", "FINISHED", 0.0
        self._media_status(0)

    def take_over(self, app_id: str | None, name: str) -> None:
        self.app, self.app_name = app_id, name
        self.media = None
        self._receiver_status(0)

    # -- protocol ---------------------------------------------------------
    def _serve(self) -> None:
        while True:
            try:
                raw, _ = self.listener.accept()
            except OSError:
                return
            try:
                self._conn = self.ctx.wrap_socket(raw, server_side=True)
            except (OSError, ssl.SSLError):
                raw.close()
                continue
            try:
                self._read_loop(self._conn)
            except (OSError, ssl.SSLError, struct.error):
                pass

    def _read_loop(self, conn) -> None:
        while True:
            head = self._read(conn, 4)
            if not head:
                return
            body = self._read(conn, struct.unpack(">I", head)[0])
            msg = CastMessage()
            msg.ParseFromString(body)
            payload = json.loads(msg.payload_utf8)
            self._client = msg.source_id
            self._handle(msg.namespace, msg.destination_id, payload)

    @staticmethod
    def _read(conn, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf

    def _send(self, ns: str, source: str, payload: dict) -> None:
        msg = CastMessage()
        msg.protocol_version = msg.CASTV2_1_0
        msg.source_id = source
        msg.destination_id = self._client
        msg.namespace = ns
        msg.payload_type = CastMessage.STRING
        msg.payload_utf8 = json.dumps(payload)
        data = msg.SerializeToString()
        with self._send_lock:
            self._conn.sendall(struct.pack(">I", len(data)) + data)

    def _handle(self, ns: str, dest: str, p: dict) -> None:
        kind = p.get("type")
        if ns == NS_HEARTBEAT:
            if kind == "PING":
                self._send(ns, dest, {"type": "PONG"})
            return
        if ns == NS_CONNECTION:
            return
        rid = p.get("requestId", 0)
        with self.lock:
            self.received.append({**p, "_ns": ns})
            if ns == NS_RECEIVER:
                if kind == "LAUNCH":
                    self.app, self.app_name = p["appId"], APP_NAMES.get(p["appId"], p["appId"])
                elif kind == "SET_VOLUME":
                    self.volume = p["volume"].get("level", self.volume)
                elif kind == "STOP":
                    self.app = None
                self._receiver_status(rid)
            elif ns == NS_MEDIA:
                if kind == "LOAD":
                    self.session += 1
                    self.media = p["media"]
                    self.player_state, self.idle_reason = "PLAYING", None
                    self.current_time = p.get("currentTime") or 0.0
                    threading.Thread(target=self._fetch, args=(self.media["contentId"],), daemon=True).start()
                elif kind == "PAUSE":
                    self.player_state = "PAUSED"
                elif kind == "PLAY":
                    self.player_state = "PLAYING"
                elif kind == "SEEK":
                    self.current_time = p["currentTime"]
                elif kind == "STOP":
                    self.player_state, self.idle_reason = "IDLE", "CANCELLED"
                self._media_status(rid)

    def _receiver_status(self, rid: int) -> None:
        status: dict = {"volume": {"level": self.volume, "muted": False}}
        if self.app:
            status["applications"] = [{
                "appId": self.app, "displayName": self.app_name, "sessionId": f"session-{self.app}",
                "transportId": f"web-{self.app}", "statusText": "",
                "namespaces": [{"name": NS_MEDIA}] if self.app == "CC1AD845" else [],
            }]
        self._send(NS_RECEIVER, "receiver-0", {"type": "RECEIVER_STATUS", "requestId": rid, "status": status})

    def _media_status(self, rid: int) -> None:
        status = []
        if self.media is not None:
            s = {"mediaSessionId": self.session, "playerState": self.player_state, "currentTime": self.current_time,
                 "playbackRate": 1, "supportedMediaCommands": 15, "media": self.media,
                 "volume": {"level": self.volume, "muted": False}}
            if self.idle_reason:
                s["idleReason"] = self.idle_reason
            status.append(s)
        self._send(NS_MEDIA, f"web-{self.app}", {"type": "MEDIA_STATUS", "requestId": rid, "status": status})

    def _fetch(self, url: str) -> None:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a LAN device has no proxy
            with opener.open(urllib.request.Request(url, headers={"Range": "bytes=0-1023"}), timeout=5) as r:
                result = (url, r.status, r.headers.get("Content-Type", ""))
        except Exception as exc:
            result = (url, getattr(exc, "code", 0), "")
        with self.lock:
            self.fetched.append(result)
