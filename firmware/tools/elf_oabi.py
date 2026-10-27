"""Mark an ARM ELF executable as old-ABI (e_flags = 0).

The receiver's Linux 2.2 kernel predates ARM EABI. Our code never relies on
EABI calling conventions across the kernel boundary (syscalls use the old
"swi 0x900000+n" form), so the header just needs to look like the
executables that kernel was built to run.
"""

import struct
import sys

path = sys.argv[1]
with open(path, "r+b") as f:
    ident = f.read(16)
    if ident[:4] != b"\x7fELF" or ident[4] != 1 or ident[5] != 1:
        sys.exit(f"{path}: not a 32-bit little-endian ELF file")
    f.seek(18)
    (machine,) = struct.unpack("<H", f.read(2))
    if machine != 40:
        sys.exit(f"{path}: not an ARM executable")
    f.seek(36)
    f.write(struct.pack("<I", 0))
