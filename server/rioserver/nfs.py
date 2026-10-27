"""Portmapper, mountd and a read-only NFSv2 server for receiver netboot.

The Rio Receiver's Linux 2.2 kernel mounts its root filesystem with NFSv2 over
UDP, asking the portmapper (port 111) where mountd and nfsd live. If the
portmapper can't be reached it falls back to the well-known ports 627 (mountd)
and 2049 (nfsd), which are also the defaults here.

Everything is served from an in-memory FirmwareImage; every write operation
fails with NFSERR_ROFS.
"""

from __future__ import annotations

import logging
import struct
from typing import Callable

from .firmware import NFDIR, NFLNK, NFREG, FirmwareImage
from .rpc import Packer, RpcDispatcher, RpcProgram, Unpacker

log = logging.getLogger(__name__)

PMAP_PROG, MOUNT_PROG, NFS_PROG = 100000, 100005, 100003
IPPROTO_TCP, IPPROTO_UDP = 6, 17

NFS_OK = 0
NFSERR_PERM = 1
NFSERR_NOENT = 2
NFSERR_IO = 5
NFSERR_ACCES = 13
NFSERR_NOTDIR = 20
NFSERR_ISDIR = 21
NFSERR_ROFS = 30
NFSERR_NAMETOOLONG = 63
NFSERR_STALE = 70

FHSIZE = 32
MAX_READ = 8192
FH_MAGIC = b"RIO1"


def make_fh(image: FirmwareImage, ino: int) -> bytes:
    return FH_MAGIC + struct.pack(">II", image.generation, ino) + b"\0" * (FHSIZE - 12)


class BootServer:
    """Holds the RPC programs; `image_provider` returns the current image."""

    def __init__(self, image_provider: Callable[[], FirmwareImage | None],
                 mountd_port: int, nfs_port: int, portmap_port: int = 111,
                 on_mount: Callable[[str, str], None] | None = None):
        self.image_provider = image_provider
        self.mountd_port = mountd_port
        self.nfs_port = nfs_port
        self.portmap_port = portmap_port
        self.on_mount = on_mount
        self.mounts: dict[str, str] = {}

    def mappings(self) -> list[tuple[int, int, int, int]]:
        m = []
        for proto in (IPPROTO_UDP, IPPROTO_TCP):
            m.append((PMAP_PROG, 2, proto, self.portmap_port))
            for v in (1, 2):
                m.append((MOUNT_PROG, v, proto, self.mountd_port))
            m.append((NFS_PROG, 2, proto, self.nfs_port))
        return m

    # -- portmapper -----------------------------------------------------
    def portmap_v2(self, proc: int, u: Unpacker, addr) -> bytes | None:
        if proc == 0:
            return b""
        if proc in (1, 2):  # SET / UNSET: we don't accept registrations
            u.uint(); u.uint(); u.uint(); u.uint()
            return Packer().uint(0).bytes()
        if proc == 3:  # GETPORT
            prog, vers, prot, _ = u.uint(), u.uint(), u.uint(), u.uint()
            port = 0
            for p, v, pr, po in self.mappings():
                if p == prog and v == vers and pr == prot:
                    port = po
                    break
            log.debug("portmap GETPORT %d v%d proto %d from %s -> %d", prog, vers, prot, addr[0], port)
            return Packer().uint(port).bytes()
        if proc == 4:  # DUMP
            p = Packer()
            for prog, vers, prot, port in self.mappings():
                p.uint(1).uint(prog).uint(vers).uint(prot).uint(port)
            return p.uint(0).bytes()
        return None

    # -- mountd ---------------------------------------------------------
    def mount_v1(self, proc: int, u: Unpacker, addr) -> bytes | None:
        if proc == 0:
            return b""
        if proc == 1:  # MNT
            path = u.string(1024)
            image = self.image_provider()
            if image is None:
                log.warning("mount of %s from %s refused: no firmware image available", path, addr[0])
                return Packer().uint(NFSERR_NOENT).bytes()
            # Any export path works: /tftpboot/<ip> (JReceiver style),
            # /export/mercury (original Windows server) or plain "/".
            log.info("receiver %s mounted %s (firmware %s)", addr[0], path, image.name)
            self.mounts[addr[0]] = path
            if self.on_mount:
                self.on_mount(addr[0], path)
            return Packer().uint(0).fixed(make_fh(image, image.ROOT)).bytes()
        if proc == 2:  # DUMP
            p = Packer()
            for host, path in self.mounts.items():
                p.uint(1).string(host).string(path)
            return p.uint(0).bytes()
        if proc == 3:  # UMNT
            u.string(1024)
            self.mounts.pop(addr[0], None)
            return b""
        if proc == 4:  # UMNTALL
            self.mounts.pop(addr[0], None)
            return b""
        if proc == 5:  # EXPORT
            return Packer().uint(1).string("/").uint(0).uint(0).bytes()
        return None

    # -- nfs v2 ---------------------------------------------------------
    @staticmethod
    def fattr(p: Packer, image: FirmwareImage, node) -> Packer:
        nlink = 2 + sum(1 for c in node.children.values() if image.nodes[c].kind == NFDIR) \
            if node.kind == NFDIR else 1
        size = 4096 if node.kind == NFDIR else node.size
        t = node.mtime
        (p.uint(node.kind).uint(node.mode).uint(nlink).uint(0).uint(0)
         .uint(size).uint(4096).uint(node.rdev).uint((size + 4095) // 4096)
         .uint(image.generation & 0xFFFF).uint(node.ino)
         .uint(t).uint(0).uint(t).uint(0).uint(t).uint(0))
        return p

    def _resolve(self, fh: bytes):
        image = self.image_provider()
        if image is None or fh[:4] != FH_MAGIC:
            return None, None
        gen, ino = struct.unpack(">II", fh[4:12])
        if gen != image.generation:
            return image, None
        return image, image.nodes.get(ino)

    def nfs_v2(self, proc: int, u: Unpacker, addr) -> bytes | None:
        if proc in (0, 3, 7):  # NULL, ROOT (obsolete), WRITECACHE (obsolete)
            return b""
        if proc in (2, 8, 9, 10, 11, 12, 13, 14, 15):  # all modifying calls
            return Packer().uint(NFSERR_ROFS).bytes()
        if proc == 17:  # STATFS
            image, node = self._resolve(u.fixed(FHSIZE))
            if node is None:
                return Packer().uint(NFSERR_STALE).bytes()
            blocks = max(1, image.total_bytes // 4096)
            return Packer().uint(NFS_OK).uint(MAX_READ).uint(4096).uint(blocks).uint(0).uint(0).bytes()

        fh = u.fixed(FHSIZE)
        image, node = self._resolve(fh)
        if node is None:
            return Packer().uint(NFSERR_STALE).bytes()

        if proc == 1:  # GETATTR
            return self.fattr(Packer().uint(NFS_OK), image, node).bytes()

        if proc == 4:  # LOOKUP
            name = u.string(1024)
            if node.kind != NFDIR:
                return Packer().uint(NFSERR_NOTDIR).bytes()
            if len(name) > 255:
                return Packer().uint(NFSERR_NAMETOOLONG).bytes()
            if name == ".":
                child = node
            elif name == "..":
                child = image.nodes[node.parent]
            else:
                ino = node.children.get(name)
                if ino is None:
                    log.debug("LOOKUP %s/%s from %s: not found", node.name, name, addr[0])
                    return Packer().uint(NFSERR_NOENT).bytes()
                child = image.nodes[ino]
            p = Packer().uint(NFS_OK).fixed(make_fh(image, child.ino))
            return self.fattr(p, image, child).bytes()

        if proc == 5:  # READLINK
            if node.kind != NFLNK:
                return Packer().uint(NFSERR_IO).bytes()
            return Packer().uint(NFS_OK).string(node.target).bytes()

        if proc == 6:  # READ
            offset, count, _total = u.uint(), u.uint(), u.uint()
            if node.kind == NFDIR:
                return Packer().uint(NFSERR_ISDIR).bytes()
            count = min(count, MAX_READ)
            data = node.data[offset:offset + count] if node.kind == NFREG else b""
            p = self.fattr(Packer().uint(NFS_OK), image, node)
            return p.opaque(data).bytes()

        if proc == 16:  # READDIR
            cookie = struct.unpack(">I", u.fixed(4))[0]
            count = u.uint()
            if node.kind != NFDIR:
                return Packer().uint(NFSERR_NOTDIR).bytes()
            entries = [(".", node.ino), ("..", node.parent)] + list(node.children.items())
            p = Packer().uint(NFS_OK)
            used = 16  # status + list terminator + eof + slack
            idx = cookie
            while idx < len(entries):
                name, ino = entries[idx]
                raw = name.encode("latin-1", "replace")
                need = 16 + ((len(raw) + 3) & ~3)
                if used + need > count:
                    break
                p.uint(1).uint(ino).opaque(raw).fixed(struct.pack(">I", idx + 1))
                used += need
                idx += 1
            eof = idx >= len(entries)
            return p.uint(0).uint(1 if eof else 0).bytes()

        return None

    def dispatchers(self) -> dict[str, RpcDispatcher]:
        return {
            "portmap": RpcDispatcher([RpcProgram(PMAP_PROG, {2: self.portmap_v2}, "portmap")]),
            "mountd": RpcDispatcher([RpcProgram(MOUNT_PROG, {1: self.mount_v1, 2: self.mount_v1}, "mountd")]),
            "nfsd": RpcDispatcher([RpcProgram(NFS_PROG, {2: self.nfs_v2}, "nfsd")]),
        }
