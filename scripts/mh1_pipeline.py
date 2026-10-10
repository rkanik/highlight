#!/usr/bin/env python3
"""MH1 end-to-end: detect → assemble landscape match highlight (≤10 min)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path("V:/highlight/scripts")


def main() -> int:
    py = sys.executable
    steps = [
        [py, "-u", str(SCRIPTS / "mh1_detect.py")],
        [py, "-u", str(SCRIPTS / "mh1_assemble.py")],
    ]
    for cmd in steps:
        print("\n====", Path(cmd[-1]).name, "====", flush=True)
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print("FAILED", cmd[-1], "code", r.returncode, flush=True)
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
