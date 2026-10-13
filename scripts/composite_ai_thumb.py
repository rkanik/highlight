#!/usr/bin/env python3
"""
Composite AUGNIIK thumb from AI hero base.

- Exactly ONE logo: augniik-profile.jpg (circular badge)
- Hook text from AI title (no kill counts)
- Landscape 16:9 primary; optional 9:16 via cover-crop (never stretch)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.thumb import composite, hook_from_title  # noqa: E402

LOGO = ROOT / "augniik-profile.jpg"


def main() -> int:
    # args: ai_base out_dir title [--shorts]
    if len(sys.argv) < 4:
        print("usage: composite_ai_thumb.py <ai_base> <out_dir> <title> [--shorts]")
        return 2
    ai_base = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    title = sys.argv[3]
    want_shorts = "--shorts" in sys.argv
    if not ai_base.exists():
        print("missing AI base", ai_base)
        return 1
    if not LOGO.exists():
        print("missing logo", LOGO)
        return 1

    hook = hook_from_title(title)
    stem = out_dir.name
    # MH1 / YT landscape primary
    for name, size, tag in [
        (f"{stem}.jpg", (1920, 1080), "PUBG"),
        (f"{stem} YT.jpg", (1280, 720), "PUBG"),
        *([(f"{stem} Shorts.jpg", (1080, 1920), "PUBG SHORTS")] if want_shorts else []),
    ]:
        out = out_dir / name
        composite(ai_base, out, hook, size, LOGO, tag=tag)
        print("thumb ->", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
