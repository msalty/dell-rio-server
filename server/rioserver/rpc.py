"""Minimal ONC RPC (RFC 1831) / XDR (RFC 1832) server plumbing.

Just enough to host a portmapper, mountd and a read-only NFSv2 server for the
Rio Receiver's network boot. Requests arrive over UDP (what the receiver's
Linux 2.2 kernel uses) or TCP (handy for rpcinfo and other tools).
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Callable

log = logging.getLogger(__name__)

CALL, REPLY = 0, 1
MSG_ACCEPTED, MSG_DENIED = 0, 1
SUCCESS, PROG_UNAVAIL, PROG_MISMATCH, PROC_UNAVAIL, GARBAGE_ARGS, SYSTEM_ERR = range(6)
AUTH_NULL = 0


class XDRError(Exception):
    pass


class Unpacker:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def uint(self) -> int:
        if self.pos + 4 > len(self.data):
            raise XDRError("short read")
        (v,) = struct.unpack_from(">I", self.data, self.pos)
        self.pos += 4
        return v

    def int(self) -> int:
        v = self.uint()
        return v - (1 << 32) if v & 0x80000000 else v

    def fixed(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise XDRError("short read")
        v = self.data[self.pos:self.pos + n]
        self.pos += (n + 3) & ~3
        return v

    def opaque(self, limit: int = 1 << 20) -> bytes:
        n = self.uint()
        if n > limit:
            raise XDRError("opaque too long")
        return self.fixed(n)

    def string(self, limit: int = 4096) -> str:
        return self.opaque(limit).decode("latin-1")


class Packer:
    def __init__(self):
        self.parts: list[bytes] = []

    def uint(self, v: int) -> "Packer":
        self.parts.append(struct.pack(">I", v & 0xFFFFFFFF))
        return self

    def int(self, v: int) -> "Packer":
        self.parts.append(struct.pack(">i", v))
        return self

    def fixed(self, b: bytes) -> "Packer":
        self.parts.append(b)
        pad = (-len(b)) % 4
        if pad:
            self.parts.append(b"\0" * pad)
        return self

    def opaque(self, b: bytes) -> "Packer":
        self.uint(len(b))
        return self.fixed(b)

    def string(self, s: str | bytes) -> "Packer":
        return self.opaque(s.encode("latin-1", "replace") if isinstance(s, str) else s)

    def bytes(self) -> bytes:
        return b"".join(self.parts)


# A handler receives (proc, args_unpacker, client_addr) and returns the encoded
# result body, or None for PROC_UNAVAIL.
Handler = Callable[[int, Unpacker, tuple], "bytes | None"]


class RpcProgram:
    def __init__(self, prog: int, versions: dict[int, Handler], name: str):
        self.prog = prog
        self.versions = versions
        self.name = name


class RpcDispatcher:
    def __init__(self, programs: list[RpcProgram]):
        self.programs = {p.prog: p for p in programs}

    def handle(self, data: bytes, addr) -> bytes | None:
        u = Unpacker(data)
        try:
            xid = u.uint()
            if u.uint() != CALL:
                return None
            rpcvers = u.uint()
            prog, vers, proc = u.uint(), u.uint(), u.uint()
            u.uint(); u.opaque(400)  # credentials
            u.uint(); u.opaque(400)  # verifier
        except XDRError:
            return None
        head = Packer().uint(xid).uint(REPLY)
        if rpcvers != 2:
            # MSG_DENIED / RPC_MISMATCH
            return head.uint(MSG_DENIED).uint(0).uint(2).uint(2).bytes()
        head.uint(MSG_ACCEPTED).uint(AUTH_NULL).uint(0)
        program = self.programs.get(prog)
        if program is None:
            return head.uint(PROG_UNAVAIL).bytes()
        handler = program.versions.get(vers)
        if handler is None:
            lo, hi = min(program.versions), max(program.versions)
            return head.uint(PROG_MISMATCH).uint(lo).uint(hi).bytes()
        try:
            body = handler(proc, u, addr)
        except XDRError:
            return head.uint(GARBAGE_ARGS).bytes()
        except Exception:
            log.exception("%s v%d proc %d failed", program.name, vers, proc)
            return head.uint(SYSTEM_ERR).bytes()
        if body is None:
            return head.uint(PROC_UNAVAIL).bytes()
        return head.uint(SUCCESS).bytes() + body


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, dispatcher: RpcDispatcher, name: str):
        self.dispatcher = dispatcher
        self.name = name
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        reply = self.dispatcher.handle(data, addr)
        if reply is not None:
            self.transport.sendto(reply, addr)


async def _tcp_client(dispatcher: RpcDispatcher, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
    addr = writer.get_extra_info("peername")
    try:
        while True:
            record = b""
            while True:
                hdr = await reader.readexactly(4)
                (mark,) = struct.unpack(">I", hdr)
                length = mark & 0x7FFFFFFF
                if length > 1 << 20:
                    return
                record += await reader.readexactly(length)
                if mark & 0x80000000:
                    break
            reply = dispatcher.handle(record, addr)
            if reply is not None:
                writer.write(struct.pack(">I", 0x80000000 | len(reply)) + reply)
                await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()


async def serve_rpc(dispatcher: RpcDispatcher, host: str, port: int, name: str,
                    tcp: bool = True) -> list:
    """Start UDP (and optionally TCP) listeners. Returns objects to close."""
    loop = asyncio.get_running_loop()
    closers = []
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _UdpProtocol(dispatcher, name), local_addr=(host, port), reuse_port=False
    )
    closers.append(transport)
    if tcp:
        server = await asyncio.start_server(
            lambda r, w: _tcp_client(dispatcher, r, w), host, port, reuse_address=True
        )
        closers.append(server)
    return closers
