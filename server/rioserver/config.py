"""Runtime configuration, read from environment variables.

Every setting has a sensible default so the container works with nothing more
than a music volume mounted at /music.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass
class Config:
    music_dir: Path = Path("/music")
    data_dir: Path = Path("/data")
    firmware_dir: Path = Path("/firmware")

    # Address advertised to receivers. Empty means auto-detect per request.
    server_ip: str = ""

    # The original Windows server used 12078 for the music protocol. The web UI
    # is served from the same application on every port in http_ports.
    http_port: int = 12078
    web_port: int = 8080

    ssdp_port: int = 21075
    portmap_port: int = 111
    # Linux 2.2's root-NFS falls back to 2049/627 if the portmapper can't be
    # reached, so those are the defaults here too.
    nfs_port: int = 2049
    mountd_port: int = 627
    enable_boot: bool = True

    # Which firmware image to serve to receivers over NFS:
    #   "ng"       - the RioServer NG player built into this image (default)
    #   "rioplay"  - David Flowerday's RioPlay 0.29alpha2
    #   anything else is a file name in <data_dir>/firmware or firmware_dir
    #   (e.g. "receiver.arf" copied from an original Rio/Dell install).
    firmware: str = "ng"

    rescan_interval: int = 900  # seconds between full rescans (0 = never)
    watch: bool = True  # react to filesystem changes immediately
    watch_polling: bool = False  # use polling instead of inotify (NAS/SMB volumes)

    # Show "Name (count)" in the stock player's menus, as JReceiver did.
    classic_show_counts: bool = False

    log_level: str = "INFO"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        c = cls()
        c.music_dir = Path(os.environ.get("RIO_MUSIC_DIR", str(c.music_dir)))
        c.data_dir = Path(os.environ.get("RIO_DATA_DIR", str(c.data_dir)))
        c.firmware_dir = Path(os.environ.get("RIO_FIRMWARE_DIR", str(c.firmware_dir)))
        c.server_ip = os.environ.get("RIO_SERVER_IP", c.server_ip).strip()
        c.http_port = _env_int("RIO_HTTP_PORT", c.http_port)
        c.web_port = _env_int("RIO_WEB_PORT", c.web_port)
        c.ssdp_port = _env_int("RIO_SSDP_PORT", c.ssdp_port)
        c.portmap_port = _env_int("RIO_PORTMAP_PORT", c.portmap_port)
        c.nfs_port = _env_int("RIO_NFS_PORT", c.nfs_port)
        c.mountd_port = _env_int("RIO_MOUNTD_PORT", c.mountd_port)
        c.enable_boot = _env_bool("RIO_ENABLE_BOOT", c.enable_boot)
        c.firmware = os.environ.get("RIO_FIRMWARE", c.firmware).strip() or "ng"
        c.rescan_interval = _env_int("RIO_RESCAN_INTERVAL", c.rescan_interval)
        c.watch = _env_bool("RIO_WATCH", c.watch)
        c.watch_polling = _env_bool("RIO_WATCH_POLLING", c.watch_polling)
        c.classic_show_counts = _env_bool("RIO_CLASSIC_SHOW_COUNTS", c.classic_show_counts)
        c.log_level = os.environ.get("RIO_LOG_LEVEL", c.log_level).upper()
        return c

    @property
    def http_ports(self) -> list[int]:
        ports = [self.http_port]
        if self.web_port and self.web_port != self.http_port:
            ports.append(self.web_port)
        return ports
