"""Wires the library, boot services and HTTP protocols together."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

from .config import Config
from .firmware import FirmwareStore
from .library import Library
from .nfs import BootServer
from .receivers import ReceiverRegistry
from .rio_classic import ClassicProtocol
from .rio_ng import NGProtocol
from .rpc import RpcDispatcher, serve_rpc
from .ssdp import local_ip_for, serve_ssdp
from .watcher import LibraryWatcher
from .web_api import WebAPI

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "web"


@dataclass
class Context:
    config: Config
    library: Library
    receivers: ReceiverRegistry
    watcher: LibraryWatcher | None = None
    firmware: FirmwareStore | None = None


def build_app(ctx: Context) -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_routes(ClassicProtocol(ctx).routes())
    app.router.add_routes(NGProtocol(ctx).routes())
    app.router.add_routes(WebAPI(ctx).routes())

    async def index(request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


class FirmwareChoice:
    """Remembers the web UI's firmware choice across restarts."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> str | None:
        try:
            return self.path.read_text().strip() or None
        except OSError:
            return None

    def save(self, name: str) -> None:
        try:
            self.path.write_text(name + "\n")
        except OSError as exc:
            log.warning("could not remember firmware choice: %s", exc)


async def start_boot_services(ctx: Context) -> list:
    cfg = ctx.config
    closers = []
    boot = BootServer(ctx.firmware.image, cfg.mountd_port, cfg.nfs_port, cfg.portmap_port,
                      on_mount=lambda ip, path: ctx.receivers.touch(ip, stage="booting"))
    d = boot.dispatchers()
    # Programs that share a port share a dispatcher.
    by_port: dict[int, list] = {}
    for name, port in (("portmap", cfg.portmap_port), ("mountd", cfg.mountd_port), ("nfsd", cfg.nfs_port)):
        by_port.setdefault(port, []).append(name)
    for port, names in by_port.items():
        disp = RpcDispatcher([p for n in names for p in d[n].programs.values()])
        try:
            closers += await serve_rpc(disp, "0.0.0.0", port, "+".join(names))
            log.info("%s listening on port %d (udp/tcp)", "+".join(names), port)
        except OSError as exc:
            level = logging.WARNING if names == ["portmap"] else logging.ERROR
            log.log(level, "could not bind %s to port %d: %s%s", "+".join(names), port, exc,
                    " (receivers will fall back to the default mountd/nfsd ports)"
                    if names == ["portmap"] else "")
    return closers


async def run(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    library = Library(cfg.music_dir, cfg.data_dir / "library.db")
    ctx = Context(cfg, library, ReceiverRegistry())
    ctx.watcher = LibraryWatcher(library, cfg.rescan_interval, cfg.watch, cfg.watch_polling)
    ctx.watcher.start()

    closers = []
    if cfg.enable_boot:
        choice_store = FirmwareChoice(cfg.data_dir / "firmware.txt")
        # Lets firmware find the music server even if SSDP replies are lost.
        server_line = f"{cfg.server_ip or local_ip_for('')}:{cfg.http_port}\n".encode()
        user_fw = cfg.data_dir / "firmware"
        user_fw.mkdir(exist_ok=True)
        ctx.firmware = FirmwareStore([user_fw, cfg.firmware_dir], choice_store.load() or cfg.firmware,
                                     overlay={"/etc/rio-server": server_line})
        original_select = ctx.firmware.select

        def select_and_save(name: str) -> None:
            original_select(name)
            choice_store.save(name)

        ctx.firmware.select = select_and_save
        ctx.firmware.image()
        closers += await start_boot_services(ctx)

    def discovered(ip: str, kind: str, mac: str | None) -> None:
        ctx.receivers.touch(ip, mac=mac, stage="booting" if kind == "boot" else "running")

    try:
        closers.append(await serve_ssdp(cfg.ssdp_port, cfg.server_ip, cfg.http_port, discovered))
        log.info("SSDP discovery listening on udp/%d", cfg.ssdp_port)
    except OSError as exc:
        log.error("could not bind SSDP port %d: %s", cfg.ssdp_port, exc)

    app = build_app(ctx)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    for port in cfg.http_ports:
        await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info("HTTP listening on port %d", port)
    log.info("RioServer NG ready: web interface at http://<this-host>:%d/", cfg.web_port or cfg.http_port)

    try:
        await asyncio.Event().wait()
    finally:
        ctx.watcher.stop()
        for c in closers:
            c.close()
        await runner.cleanup()
