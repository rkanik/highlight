#!/usr/bin/env python3
"""
Composite AUGNIIK thumb from AI hero base.

- Exactly ONE logo: augniik-profile.jpg (circular badge)
- Hook text from AI title (no kill counts)
- Landscape 16:9 primary; optional 9:16 via cover-crop (never stretch)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path("V:/highlight")
LOGO = ROOT / "augniik-profile.jpg"
CYAN = (0, 210, 255)
ORANGE = (255, 140, 20)
YELLOW = (255, 230, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def load_font(size: int):
    for fp in (
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/seguibl.ttf",
    ):
        try:
            return ImageFont.truetype(fp, size)
        except OSError:
            continue
    return ImageFont.load_default()


def stroke_text(draw, xy, text, font, fill, stroke=BLACK, width=8):
    x, y = xy
    for dx in range(-width, width + 1, 2):
        for dy in range(-width, width + 1, 2):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke)
    draw.text((x, y), text, font=font, fill=fill)


def make_logo_badge(size: int = 150, border: int = 9) -> Image.Image:
    av = Image.open(LOGO).convert("RGBA")
    w, h = av.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    av = av.crop((left, top, left + side, top + side))
    av = av.resize((size, size), Image.Resampling.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    av.putalpha(mask)

    pad = border + 10
    canvas_size = size + pad * 2
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(canvas)
    cx = cy = canvas_size // 2
    r = size // 2 + border
    cd.ellipse([cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4], outline=(*ORANGE, 180), width=3)
    cd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    cd.ellipse(
        [cx - size // 2 - 4, cy - size // 2 - 4, cx + size // 2 + 4, cy + size // 2 + 4],
        outline=CYAN,
        width=5,
    )
    canvas.paste(av, (pad, pad), av)
    return canvas


def cover_resize(im: Image.Image, tw: int, th: int) -> Image.Image:
    """Cover-crop resize — never stretch."""
    im = im.convert("RGB")
    sw, sh = im.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def wrap_lines(draw, text: str, font, max_w: float) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines[:3]


def composite(
    ai_base: Path,
    out_jpg: Path,
    hook: str,
    size: tuple[int, int],
    tag: str = "PUBG",
) -> None:
    W, H = size
    bg = cover_resize(Image.open(ai_base), W, H)
    bg = ImageEnhance.Contrast(bg).enhance(1.2)
    bg = ImageEnhance.Color(bg).enhance(1.15)

    base = bg.convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # bottom plate for hook
    band0 = int(H * 0.58)
    for y in range(band0, H):
        t = (y - band0) / max(1, H - band0)
        od.line([(0, y), (W, y)], fill=(0, 0, 0, min(220, int(40 + 190 * t ** 1.1))))
    # brand rails
    rail = max(10, W // 90)
    od.rectangle([0, 0, rail, H], fill=(*CYAN, 200))
    od.rectangle([W - rail, 0, W, H], fill=(*ORANGE, 200))

    img = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(img)

    # font scale by height
    f_hook = load_font(max(48, H // 12))
    f_tag = load_font(max(32, H // 22))
    lines = wrap_lines(draw, hook.upper(), f_hook, W * 0.88)
    y = int(H * 0.66)
    for line in lines:
        tw = draw.textlength(line, font=f_hook)
        stroke_text(draw, ((W - tw) / 2, y), line, f_hook, YELLOW, BLACK, width=max(5, H // 140))
        y += int(f_hook.size * 1.05)

    tw = draw.textlength(tag, font=f_tag)
    stroke_text(draw, ((W - tw) / 2, y + 8), tag, f_tag, WHITE, BLACK, width=4)

    # ONE logo only — bottom-left
    logo = make_logo_badge(size=max(90, H // 7), border=max(6, H // 100))
    img.paste(logo, (rail + 12, H - logo.size[1] - 20), logo)

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_jpg, quality=96)
    print("thumb ->", out_jpg, flush=True)


def hook_from_title(title: str) -> str:
    hook = title.split("|")[0].strip()
    hook = re.sub(r"\s+", " ", hook)
    return hook


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
    composite(ai_base, out_dir / f"{stem}.jpg", hook, (1920, 1080), tag="PUBG")
    composite(ai_base, out_dir / f"{stem} YT.jpg", hook, (1280, 720), tag="PUBG")
    if want_shorts:
        composite(ai_base, out_dir / f"{stem} Shorts.jpg", hook, (1080, 1920), tag="PUBG SHORTS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
