#!/usr/bin/env python3
"""Strip the Debian-trixie shared-library overlay a pip layer drops onto bookworm.

Installing the PaddleOCR dependency set somehow drops a *second*, newer
(Debian-trixie / gcc-13, glibc 2.38/2.39) copy of a whole swathe of core system
libraries into /usr/lib/x86_64-linux-gnu alongside the bookworm originals, and
ldconfig then points each soname at the higher (trixie) version. Those libs
require GLIBC_2.38+, which this glibc-2.36 base does not provide, so EVERY native
import (pymupdf._extra, cv2, paddle) and core tool (curl, apt, sed, tar) breaks
with "version `GLIBC_2.38' not found". The plain base image is clean — only our
build introduces these, and (frustratingly) non-deterministically — so this runs
unconditionally as a safety net.

The bookworm originals are always still present (the trixie libs are *duplicates*
with higher version numbers), so for every soname that ends up with 2+ real
versioned files we keep the lowest version and delete the rest; ldconfig then
repoints the symlinks at the bookworm libs. Verified on a real node: paddle, cv2
and pymupdf import and a live OCR inference all succeed afterwards.

A few packages (notably libldap) are *replaced* rather than duplicated and have
no bookworm version to fall back to; the Dockerfile reinstalls those from apt
right after this script runs (apt itself works again once the dup glibc-2.38
libs are gone).
"""

import os
import re
import subprocess
from collections import defaultdict

LIBDIR = "/usr/lib/x86_64-linux-gnu"
# Matches "<name>.so.<version>" real files, e.g. libgnutls.so.30.40.3
_VER = re.compile(r"^(.*\.so)\.(\d[\d.]*)$")


def main() -> None:
    groups: dict[str, list[tuple[tuple[int, ...], str]]] = defaultdict(list)
    for name in os.listdir(LIBDIR):
        path = os.path.join(LIBDIR, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        m = _VER.match(name)
        if not m:
            continue
        version = tuple(int(x) for x in m.group(2).split("."))
        groups[m.group(1)].append((version, name))

    removed: list[str] = []
    for items in groups.values():
        if len(items) < 2:
            continue
        items.sort()  # ascending by version tuple; items[0] is the bookworm lib
        for _, name in items[1:]:
            os.remove(os.path.join(LIBDIR, name))
            removed.append(name)

    subprocess.run(["ldconfig"], check=True)
    print(f"clean_libs: removed {len(removed)} trixie duplicate libs: {sorted(removed)}")


if __name__ == "__main__":
    main()
