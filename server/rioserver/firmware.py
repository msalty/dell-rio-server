"""Receiver firmware images.

A Rio Receiver firmware image ("receiver.arf") is a plain tar archive of the
root filesystem the receiver boots over NFS: a kernel (/zImage), the HomePNA
driver blob (/il-binary.o), /sbin/init, device nodes and so on.

Rather than unpacking it to disk (device nodes would need root and mknod), the
archive is loaded into an in-memory tree that the NFS server serves directly.
"""

from __future__ import annotations

import hashlib
import io
import logging
import stat
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# NFSv2 ftype values
NFNON, NFREG, NFDIR, NFBLK, NFCHR, NFLNK = 0, 1, 2, 3, 4, 5

_KIND_BITS = {
    NFREG: stat.S_IFREG,
    NFDIR: stat.S_IFDIR,
    NFBLK: stat.S_IFBLK,
    NFCHR: stat.S_IFCHR,
    NFLNK: stat.S_IFLNK,
}

BUILTIN_IMAGES = {
    "ng": "receiver-ng.arf",
    "rioplay": "receiver-rioplay.arf",
}


@dataclass
class Node:
    ino: int
    name: str
    kind: int
    perm: int
    parent: int
    size: int = 0
    mtime: int = 0
    data: bytes = b""
    target: str = ""
    rdev: int = 0
    children: dict[str, int] = field(default_factory=dict)

    @property
    def mode(self) -> int:
        return _KIND_BITS.get(self.kind, 0) | (self.perm & 0o7777)


class FirmwareImage:
    """An in-memory, read-only filesystem tree built from a tar archive."""

    ROOT = 1

    def __init__(self, name: str, archive: bytes, overlay: dict[str, bytes] | None = None):
        self.name = name
        self.generation = int.from_bytes(hashlib.sha1(archive).digest()[:4], "big")
        self.nodes: dict[int, Node] = {}
        now = int(time.time())
        self._add(Node(self.ROOT, "", NFDIR, 0o755, self.ROOT, mtime=now))
        self._load(archive)
        for path, content in (overlay or {}).items():
            self.add_file(path, content, 0o644, now)
        self.total_bytes = sum(n.size for n in self.nodes.values() if n.kind == NFREG)

    # -- construction ---------------------------------------------------
    def _add(self, node: Node) -> Node:
        self.nodes[node.ino] = node
        if node.ino != self.ROOT:
            self.nodes[node.parent].children[node.name] = node.ino
        return node

    def _dir(self, parts: list[str], mtime: int) -> int:
        cur = self.ROOT
        for part in parts:
            node = self.nodes[cur]
            nxt = node.children.get(part)
            if nxt is None:
                nxt = self._add(Node(len(self.nodes) + 1, part, NFDIR, 0o755, cur, mtime=mtime)).ino
            cur = nxt
        return cur

    def add_file(self, path: str, content: bytes, perm: int, mtime: int) -> None:
        parts = [p for p in path.strip("/").split("/") if p]
        parent = self._dir(parts[:-1], mtime)
        existing = self.nodes[parent].children.get(parts[-1])
        if existing is not None:
            n = self.nodes[existing]
            n.kind, n.data, n.size, n.perm, n.mtime = NFREG, content, len(content), perm, mtime
            return
        self._add(Node(len(self.nodes) + 1, parts[-1], NFREG, perm, parent,
                       size=len(content), mtime=mtime, data=content))

    def _load(self, archive: bytes) -> None:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tf:
            members = tf.getmembers()
            by_name = {}
            for m in members:
                parts = [p for p in m.name.split("/") if p not in ("", ".")]
                if not parts:
                    continue
                if any(p == ".." for p in parts):
                    log.warning("ignoring unsafe path %r in firmware", m.name)
                    continue
                mtime = int(m.mtime)
                if m.isdir():
                    ino = self._dir(parts, mtime)
                    self.nodes[ino].perm = m.mode & 0o7777
                    self.nodes[ino].mtime = mtime
                    continue
                parent = self._dir(parts[:-1], mtime)
                node = Node(len(self.nodes) + 1, parts[-1], NFNON, m.mode & 0o7777, parent, mtime=mtime)
                if m.isreg():
                    node.kind = NFREG
                    node.data = tf.extractfile(m).read()
                elif m.islnk():
                    src = by_name.get(m.linkname.lstrip("./"))
                    node.kind = NFREG
                    node.data = src.data if src else b""
                elif m.issym():
                    node.kind = NFLNK
                    node.target = m.linkname
                    node.data = m.linkname.encode()
                elif m.ischr() or m.isblk():
                    node.kind = NFCHR if m.ischr() else NFBLK
                    # Linux 2.2 kdev_t: 8-bit major, 8-bit minor.
                    node.rdev = ((m.devmajor & 0xFF) << 8) | (m.devminor & 0xFF)
                else:
                    continue
                node.size = len(node.data)
                existing = self.nodes[parent].children.get(node.name)
                if existing is not None:
                    node.ino = existing
                    node.children = self.nodes[existing].children
                    self.nodes[existing] = node
                else:
                    self._add(node)
                by_name["/".join(parts)] = node

    # -- lookups --------------------------------------------------------
    def lookup_path(self, path: str) -> Node | None:
        cur = self.nodes[self.ROOT]
        for part in [p for p in path.split("/") if p not in ("", ".")]:
            if part == "..":
                cur = self.nodes[cur.parent]
                continue
            ino = cur.children.get(part)
            if ino is None:
                return None
            cur = self.nodes[ino]
        return cur

    def file_count(self) -> int:
        return sum(1 for n in self.nodes.values() if n.kind != NFDIR)


class FirmwareStore:
    """Resolves the configured image and reloads it when the file changes."""

    def __init__(self, firmware_dirs: list[Path], choice: str, overlay: dict[str, bytes] | None = None):
        # Earlier directories win, so a user's image can shadow a built-in one.
        self.firmware_dirs = [Path(d) for d in firmware_dirs]
        self.choice = choice
        self.overlay = overlay or {}
        self._image: FirmwareImage | None = None
        self._loaded_from: tuple[Path, float] | None = None

    def available(self) -> list[dict]:
        out, seen = [], set()
        for d in self.firmware_dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix.lower() in (".arf", ".tar") and p.name not in seen:
                    seen.add(p.name)
                    alias = next((k for k, v in BUILTIN_IMAGES.items() if v == p.name), None)
                    out.append({"name": alias or p.name, "file": p.name, "size": p.stat().st_size})
        return out

    @staticmethod
    def find(dirs: list[Path], name: str) -> Path:
        name = Path(name).name
        for d in dirs:
            if (d / name).is_file():
                return d / name
        return dirs[-1] / name

    def path(self) -> Path:
        return self.find(self.firmware_dirs, BUILTIN_IMAGES.get(self.choice, self.choice))

    def select(self, choice: str) -> None:
        self.choice = choice
        self._image = None
        self._loaded_from = None

    def image(self) -> FirmwareImage | None:
        path = self.path()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            if self._image is None:
                log.error("firmware image %s not found", path)
            return self._image
        if self._loaded_from != (path, mtime):
            try:
                self._image = FirmwareImage(self.choice, path.read_bytes(), self.overlay)
            except (tarfile.TarError, OSError) as exc:
                log.error("could not load firmware %s: %s", path, exc)
                return self._image
            self._loaded_from = (path, mtime)
            log.info("serving firmware %s (%d files, %d bytes)", path.name,
                     self._image.file_count(), self._image.total_bytes)
        return self._image
