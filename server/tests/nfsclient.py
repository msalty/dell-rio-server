"""Tiny NFSv2-over-UDP client used to test the boot server the way the
receiver's kernel uses it: portmapper -> mountd -> LOOKUP/READ."""

from __future__ import annotations

import os
import socket
import struct

from rioserver.rpc import Packer, Unpacker


class RpcClient:
    def __init__(self, host: str, port: int, prog: int, vers: int):
        self.addr = (host, port)
        self.prog, self.vers = prog, vers
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(3)

    def call(self, proc: int, args: bytes = b"") -> Unpacker:
        xid = struct.unpack(">I", os.urandom(4))[0]
        msg = (Packer().uint(xid).uint(0).uint(2).uint(self.prog).uint(self.vers).uint(proc)
               .uint(0).opaque(b"").uint(0).opaque(b"").bytes() + args)
        self.sock.sendto(msg, self.addr)
        data, _ = self.sock.recvfrom(65536)
        u = Unpacker(data)
        assert u.uint() == xid
        assert u.uint() == 1  # REPLY
        assert u.uint() == 0  # accepted
        u.uint(); u.opaque()
        stat = u.uint()
        if stat != 0:
            raise RuntimeError(f"rpc accept_stat {stat}")
        return u


def getport(host: str, pm_port: int, prog: int, vers: int) -> int:
    c = RpcClient(host, pm_port, 100000, 2)
    return c.call(3, Packer().uint(prog).uint(vers).uint(17).uint(0).bytes()).uint()


def read_fattr(u: Unpacker) -> dict:
    keys = ["type", "mode", "nlink", "uid", "gid", "size", "blocksize", "rdev", "blocks",
            "fsid", "fileid", "atime", "atime_us", "mtime", "mtime_us", "ctime", "ctime_us"]
    return {k: u.uint() for k in keys}


class NfsClient:
    def __init__(self, host: str, mount_port: int, nfs_port: int):
        self.mnt = RpcClient(host, mount_port, 100005, 1)
        self.nfs = RpcClient(host, nfs_port, 100003, 2)

    def mount(self, path: str) -> bytes:
        u = self.mnt.call(1, Packer().string(path).bytes())
        assert u.uint() == 0
        return u.fixed(32)

    def lookup(self, dirfh: bytes, name: str) -> tuple[bytes, dict]:
        u = self.nfs.call(4, Packer().fixed(dirfh).string(name).bytes())
        st = u.uint()
        if st != 0:
            raise FileNotFoundError(name)
        fh = u.fixed(32)
        return fh, read_fattr(u)

    def walk(self, root: bytes, path: str) -> tuple[bytes, dict]:
        fh, attr = root, self.getattr(root)
        for part in [p for p in path.split("/") if p]:
            fh, attr = self.lookup(fh, part)
        return fh, attr

    def getattr(self, fh: bytes) -> dict:
        u = self.nfs.call(1, Packer().fixed(fh).bytes())
        assert u.uint() == 0
        return read_fattr(u)

    def read(self, fh: bytes, size: int, chunk: int = 4096) -> bytes:
        out = b""
        while len(out) < size:
            u = self.nfs.call(6, Packer().fixed(fh).uint(len(out)).uint(chunk).uint(0).bytes())
            assert u.uint() == 0
            read_fattr(u)
            data = u.opaque()
            if not data:
                break
            out += data
        return out

    def readlink(self, fh: bytes) -> str:
        u = self.nfs.call(5, Packer().fixed(fh).bytes())
        assert u.uint() == 0
        return u.string()

    def readdir(self, fh: bytes, count: int = 1024) -> list[str]:
        names, cookie = [], 0
        while True:
            u = self.nfs.call(16, Packer().fixed(fh).fixed(struct.pack(">I", cookie)).uint(count).bytes())
            assert u.uint() == 0
            while u.uint():
                u.uint()
                names.append(u.string())
                cookie = struct.unpack(">I", u.fixed(4))[0]
            if u.uint():
                return names
