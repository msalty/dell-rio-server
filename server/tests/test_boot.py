import asyncio
import io
import socket
import tarfile

import pytest

from rioserver.firmware import NFCHR, NFDIR, NFLNK, FirmwareImage
from rioserver.nfs import BootServer
from rioserver.rpc import serve_rpc
from rioserver.ssdp import SSDPResponder
from tests.nfsclient import NfsClient, getport


def make_arf() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        def add(name, data=b"", **kw):
            ti = tarfile.TarInfo(name)
            for k, v in kw.items():
                setattr(ti, k, v)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        add("./", type=tarfile.DIRTYPE, mode=0o755)
        add("./zImage", bytes(range(256)) * 100, mode=0o644)
        add("./sbin/", type=tarfile.DIRTYPE, mode=0o755)
        add("./sbin/init", b"\x7fELF" + b"x" * 5000, mode=0o755)
        add("./dev/", type=tarfile.DIRTYPE, mode=0o755)
        add("./dev/display", type=tarfile.CHRTYPE, devmajor=244, devminor=0, mode=0o666)
        add("./dev/console", type=tarfile.SYMTYPE, linkname="ttyS0", mode=0o777)
    return buf.getvalue()


def test_firmware_tree():
    img = FirmwareImage("test", make_arf(), overlay={"/etc/rio-server": b"10.0.0.2:12078\n"})
    assert img.lookup_path("/zImage").size == 25600
    disp = img.lookup_path("dev/display")
    assert disp.kind == NFCHR and disp.rdev == (244 << 8)
    assert img.lookup_path("dev/console").kind == NFLNK
    assert img.lookup_path("/etc").kind == NFDIR
    assert img.lookup_path("/etc/rio-server").data == b"10.0.0.2:12078\n"
    assert img.lookup_path("/sbin/../zImage") is not None


def free_port(kind=socket.SOCK_DGRAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_receiver_style_netboot():
    img = FirmwareImage("test", make_arf())
    pm, md, nf = free_port(), free_port(), free_port()
    boot = BootServer(lambda: img, md, nf, pm)
    d = boot.dispatchers()
    closers = []
    for name, port in (("portmap", pm), ("mountd", md), ("nfsd", nf)):
        closers += await serve_rpc(d[name], "127.0.0.1", port, name, tcp=False)
    loop = asyncio.get_running_loop()

    def client_side():
        mport = getport("127.0.0.1", pm, 100005, 1)
        nport = getport("127.0.0.1", pm, 100003, 2)
        c = NfsClient("127.0.0.1", mport, nport)
        root = c.mount("/tftpboot/192.168.1.40")
        names = c.readdir(root, count=128)
        fh, attr = c.walk(root, "zImage")
        data = c.read(fh, attr["size"], 8192)
        _, dev = c.walk(root, "dev/display")
        lfh, _ = c.walk(root, "dev/console")
        return names, data, dev, c.readlink(lfh), (mport, nport)

    try:
        names, data, dev, link, ports = await loop.run_in_executor(None, client_side)
    finally:
        for c in closers:
            c.close()
    assert ports == (md, nf)
    assert set(names) >= {".", "..", "zImage", "sbin", "dev"}
    assert data == bytes(range(256)) * 100
    assert dev["type"] == NFCHR and dev["mode"] & 0o170000 == 0o020000 and dev["rdev"] == 244 << 8
    assert link == "ttyS0"
    assert boot.mounts == {"127.0.0.1": "/tftpboot/192.168.1.40"}


@pytest.mark.parametrize("request_bytes,expected", [
    (b"upnp:uuid:1D274DB1-F053-11d3-BF72-0050DA689B2F00:90:4b:12:34:56",
     b"http://10.1.2.3/descriptor.xml\r\n"),
    (b"upnp:uuid:1D274DB0-F053-11d3-BF72-0050DA689B2F",
     b"http://10.1.2.3:12078/descriptor.xml\r\n"),
    # Dell-branded receivers send a different UUID tail
    (b"upnp:uuid:1D274DB0-F053-11d3-BF72-0050DA689B30", b"http://10.1.2.3:12078/descriptor.xml\r\n"),
])
def test_ssdp_replies(request_bytes, expected):
    r = SSDPResponder("10.1.2.3", 12078)
    assert r.reply_for(request_bytes, "10.1.2.50")[1] == expected


def test_ssdp_ignores_other_traffic():
    assert SSDPResponder("10.1.2.3", 12078).reply_for(b"M-SEARCH * HTTP/1.1", "10.1.2.50") is None
