"""Download third-party pieces of the firmware build, verifying checksums.

  libmad   MPEG audio decoder (GPL), from the MAD project on SourceForge
  rockbox-libmad
           Rockbox's copy of libmad (GPL), with hand-written ARM assembly
           for the receiver's ARMv4 CPU; the decoder the firmware uses by
           default. Fetched file by file from Rockbox's repository at a
           pinned commit.
  rioplay  RioPlay 0.29alpha2 receiver image (GPL), from SourceForge. Its
           Linux 2.2.14 kernel (zImage) and HomePNA driver (il-binary.o) are
           reused for the RioServer NG image, and the whole image is also
           offered as an alternative firmware.
"""

from __future__ import annotations

import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path

DEPS = {
    "libmad": (
        "https://downloads.sourceforge.net/project/mad/libmad/0.15.1b/libmad-0.15.1b.tar.gz",
        "bbfac3ed6bfbc2823d3775ebb931087371e142bb0e9bb1bee51a76a6e0078690",
        "libmad-0.15.1b.tar.gz",
    ),
    "rioplay": (
        "https://downloads.sourceforge.net/project/rioplay/receiver/0.29alpha2/receiver.arf-0.29alpha2",
        "37f3f1030fda41365c0d421673d305b39ab377e71a035dca8ae3693bde1b2623",
        "receiver-rioplay.arf",
    ),
}


ROCKBOX_COMMIT = "8b5767cb4933e2fbdddf9d8d4d361e82173acc33"
ROCKBOX_URL = f"https://raw.githubusercontent.com/Rockbox/rockbox/{ROCKBOX_COMMIT}/lib/rbcodec/codecs/libmad/"
ROCKBOX_FILES = [
        ("COPYING", "32b1062f7da84967e7019d01ab805935caa7ab7321a7ced0e30ebe75e5df1670"),
        ("COPYRIGHT", "9ac7e0557d59330c17c8912659d75aa53b23b3fe0e0c79d6a2236d4cb91b2346"),
        ("CREDITS", "32b14fa1e626e7d356baf71cad423dec690fa5ef1ccf2c7ebd170cf7334c09d6"),
        ("README.rockbox", "7996fdcd20a18e9bea32939bc8582d1593227903379f5beaeaac8f7a22bbcac7"),
        ("bit.c", "83dd7d92cce2737a149a0611479f898fb4b4b96214fd42fab578eded98dda77c"),
        ("bit.h", "66ff37a9226963542f006190e5bd565b486f45689c65b2aab3924d1e0554db8b"),
        ("fixed.h", "0b7f724b360f23539d5f161122f554d43a90538f9a8a95cbb1a2b7ecc9f075cc"),
        ("frame.c", "1a9763d2a43332fc12fe23a3250d9775d810c37b1394333d7d76078fe5e124a9"),
        ("frame.h", "8f1534229e494e8895f54fd872a15434bdc751d33ec5a0df6d322275e5b6dd68"),
        ("global.h", "6142f556d54336a4d318ffa132c6678b7fa1e1fe1be83faa463cb1edfb6c9793"),
        ("huffman.c", "c4545b9fced6d185b32f679873ce7b38858051112a5cae34fbdfb215e582dc59"),
        ("huffman.h", "3c9b8ef92dfc9d9d318747feb962ae9619a09aa799e2693677d3c3795fe6b4e2"),
        ("layer12.c", "046b73fe7a50ab1577ff12db1e2d7d1ee1cb97c880faf90db09bcc4236a9ab64"),
        ("layer12.h", "89e57b8deb1df35c353bedd5d332c6d132f6e367ea90fae959708a98a7fc12cd"),
        ("layer3.c", "a146e69210b519c54bc51946f045da1e240963c7fb95847883117dcc2583b509"),
        ("layer3.h", "8898aaba2b150b7365875babaff33c72578908a6131984e0f19f7f86fd08ccd8"),
        ("mad.h", "e140a73665d55fe92990cc1d857512f409839f973a18ba638e8a056df7c24b15"),
        ("mad_iram.h", "c0a085f8464729b07c9dd499cf895b74288520397cc53b6d521c7367e1c05fcf"),
        ("stream.c", "a7c729d1e6bcf24aa48cb2c0da2f5457d500fada54f5216c51d264e5289cb4a8"),
        ("stream.h", "d5897b06b51bd0c83139c08cec7f8b35561a8b6e53359783a06c24f5dc344eaf"),
        ("synth.c", "87343a650ce92fc729046ab2e5174f1e3f3dbf0d1194493376293081feb94f6a"),
        ("synth.h", "4b0e305330e1b3c9f39203ca8ce8ee835d73cf2cdf1680c3a85ea71b8dda33bc"),
        ("imdct_l_arm.S", "ff603d4ea54e901280dd0edcd159c048b3d353bf24a21f3f68e16fb98cda7d3a"),
        ("synth_full_arm.S", "610eb1fcb2418f3759be506e544d5001437882c86ecec1668d84c850ea280ed0"),
        ("D.dat", "6de68b81cb501296653d30e3a3d816950bd10ac8e7061567670793ce12ae553c"),
        ("imdct_s.dat", "adeae71077a0d94114d2bc5cb6fd30545568d23ec83bda6fca6275dbe6007068"),
        ("qc_table.dat", "e1a78a9feac0927144bc4effe6fc4b74f8ea666e695e94f9913f5a25067bd63f"),
        ("rq_table.dat", "d362eae5a0aa578c1ad85d25e1802cac769c32576665f2cecb407711c8d20a08"),
        ("sf_table.dat", "2f4ffe2428ad807431ed9f2d37c81d8737228defb503b8869b8393899549abca"),
]


def download(url: str, sha: str) -> bytes:
    print(f"fetching {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "rioserver-ng-build"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha:
        sys.exit(f"checksum mismatch for {url}: got {digest}, expected {sha}")
    return data


def fetch_rockbox(dest: Path) -> None:
    out = dest / "rockbox-libmad"
    out.mkdir(parents=True, exist_ok=True)
    for name, sha in ROCKBOX_FILES:
        f = out / name
        if f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == sha:
            continue
        f.write_bytes(download(ROCKBOX_URL + name, sha))


def fetch(name: str, dest: Path) -> Path:
    url, sha, filename = DEPS[name]
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / filename
    if out.exists() and hashlib.sha256(out.read_bytes()).hexdigest() == sha:
        return out
    out.write_bytes(download(url, sha))
    return out


def main() -> None:
    name, dest = sys.argv[1], Path(sys.argv[2])
    if name == "rockbox-libmad":
        fetch_rockbox(dest)
        return
    path = fetch(name, dest)
    if name == "libmad":
        with tarfile.open(path) as tf:
            tf.extractall(dest, filter="data")
        # Generated by libmad's configure; we only need the defaults.
        (dest / "libmad-0.15.1b" / "config.h").write_text("/* empty: settings come from CFLAGS */\n")


if __name__ == "__main__":
    main()
