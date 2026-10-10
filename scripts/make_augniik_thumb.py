#!/usr/bin/env python3
"""AUGNIIK-branded Shorts + YT thumbnails — avatar with white border, gaming CTR layout."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

ROOT = Path("V:/highlight")
# Character lobby shot = primary avatar; profile photo = single logo only (never both as chips)
CHARACTER = ROOT / "augniik-character.png"
LOGO = ROOT / "augniik-profile.jpg"
AVATAR = CHARACTER if CHARACTER.exists() else LOGO
CYAN = (0, 210, 255)
ORANGE = (255, 140, 20)
YELLOW = (255, 230, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
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


def grab_portrait(video: str, t: float, outp: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-hwaccel",
            "cuda",
            "-threads",
            "1",
            "-ss",
            f"{t:.2f}",
            "-i",
            video,
            "-frames:v",
            "1",
            "-vf",
            "crop=608:1000:656:40,scale=1080:1920",
            "-threads",
            "1",
            "-y",
            str(outp),
        ],
        check=False,
    )


def pick_action_bg(det_path: Path, work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    det = json.loads(det_path.read_text(encoding="utf-8"))
    cands = []
    for r in det:
        for e in r.get("events", []):
            if e.get("kind") in ("my_kill", "my_knock", "my_assist"):
                cands.append((r["video"], float(e["t"]) + 0.35, e["kind"]))
    best = None
    best_score = -1e9
    for i, (vid, t, kind) in enumerate(cands):
        p = work / f"bg_{i:02d}.jpg"
        if not p.exists():
            grab_portrait(vid, t, p)
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB")
        st = ImageStat.Stat(im)
        m = st.mean
        bright = sum(m) / 3
        spread = max(m) - min(m)
        var = ImageStat.Stat(im.resize((54, 96)).convert("L")).var[0]
        score = spread * 1.4 + (var**0.5) * 0.2 + (35 if kind == "my_kill" else 12)
        if bright < 50 or bright > 185:
            score -= 40
        if score > best_score:
            best_score = score
            best = p
    if best is None:
        raise SystemExit("no action frames for thumbnail")
    print(f"BG {best.name} score={best_score:.1f}", flush=True)
    return best


def make_avatar_badge(
    src: Path | None = None,
    size: int = 340,
    border: int = 14,
    face_bias: bool = True,
) -> Image.Image:
    """Circular badge with thick white border + cyan/orange accent rings."""
    path = src or AVATAR
    av = Image.open(path).convert("RGBA")
    w, h = av.size
    if face_bias and h > w * 1.15:
        # upper-body crop so helmet/face stay readable on tall lobby shots
        av = av.crop((0, 0, w, int(h * 0.72)))
        w, h = av.size
    side = min(w, h)
    top = max(0, (h - side) // 5)
    left = (w - side) // 2
    av = av.crop((left, top, left + side, top + side))
    av = av.resize((size, size), Image.Resampling.LANCZOS)
    av = ImageEnhance.Contrast(av.convert("RGB")).enhance(1.12).convert("RGBA")

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    av.putalpha(mask)

    pad = border + 12
    canvas_size = size + pad * 2
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(canvas)
    cx = cy = canvas_size // 2
    r_outer = size // 2 + border + 4

    for i in range(10, 0, -1):
        alpha = min(255, 30 + i * 14)
        cd.ellipse(
            [cx - r_outer - i, cy - r_outer - i, cx + r_outer + i, cy + r_outer + i],
            outline=(*ORANGE, alpha),
            width=2,
        )

    # thick white identity border
    cd.ellipse(
        [
            cx - size // 2 - border,
            cy - size // 2 - border,
            cx + size // 2 + border,
            cy + size // 2 + border,
        ],
        fill=WHITE,
    )
    # cyan inner ring (brand)
    cd.ellipse(
        [cx - size // 2 - 5, cy - size // 2 - 5, cx + size // 2 + 5, cy + size // 2 + 5],
        outline=CYAN,
        width=6,
    )
    canvas.paste(av, (pad, pad), av)

    # soft drop shadow under badge
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sm = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(sm).ellipse(
        [pad + 10, pad + 18, pad + size + 10, pad + size + 22], fill=170
    )
    sm = sm.filter(ImageFilter.GaussianBlur(18))
    shadow.putalpha(sm)
    return Image.alpha_composite(shadow, canvas)


def build_shorts(bg_path: Path, out_jpg: Path, count: int, subtitle: str) -> None:
    W, H = 1080, 1920
    bg = Image.open(bg_path).convert("RGB")
    bg = ImageEnhance.Contrast(bg).enhance(1.6)
    bg = ImageEnhance.Color(bg).enhance(1.5)
    bg = ImageEnhance.Sharpness(bg).enhance(1.4)
    zw, zh = int(W * 0.88), int(H * 0.88)
    top = (H - zh) // 10
    bg = bg.crop(((W - zw) // 2, top, (W + zw) // 2, top + zh)).resize(
        (W, H), Image.Resampling.LANCZOS
    )

    base = bg.convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    for y in range(0, 300):
        a = int(170 * (1 - y / 300))
        od.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    for y in range(H - 780, H):
        t = (y - (H - 780)) / 780
        od.line([(0, y), (W, y)], fill=(0, 0, 0, min(235, int(50 + 185 * t))))

    # diagonal energy band — fully below the giant number + subtitle stack
    slash = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(slash)
    sd.polygon([(-220, 1320), (1320, 1120), (1320, 1280), (-220, 1480)], fill=(0, 0, 0, 185))
    sd.line([(-220, 1320), (1320, 1120)], fill=(*CYAN, 240), width=12)
    sd.line([(-220, 1480), (1320, 1280)], fill=(*ORANGE, 240), width=12)
    overlay = Image.alpha_composite(overlay, slash)

    # brand side rails
    od.rectangle([0, 0, 22, H], fill=(*CYAN, 210))
    od.rectangle([W - 22, 0, W, H], fill=(*ORANGE, 210))

    img = Image.alpha_composite(base, overlay)

    # number glow layer
    f_num = load_font(300)
    f_sub = load_font(96)
    f_tag = load_font(50)
    num = str(count)
    tmp_draw = ImageDraw.Draw(img)
    bbox = tmp_draw.textbbox((0, 0), num, font=f_num)
    nw, nh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    nx = (W - nw) // 2
    ny = 620  # above neon slash for clean read at Shorts scale

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.text((nx, ny), num, font=f_num, fill=(*YELLOW, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(28))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)
    stroke_text(draw, (nx, ny), num, f_num, WHITE, BLACK, width=16)

    sw = draw.textlength(subtitle, font=f_sub)
    sy = ny + nh + 20
    stroke_text(draw, ((W - sw) / 2, sy), subtitle, f_sub, YELLOW, BLACK, width=9)
    # brand dual-bar under subtitle (not through the number)
    bar_y = sy + 100
    mid = W // 2
    draw.rectangle([mid - 160, bar_y, mid - 4, bar_y + 10], fill=CYAN)
    draw.rectangle([mid + 4, bar_y, mid + 160, bar_y + 10], fill=ORANGE)

    tag = "PUBG SHORTS"
    tw = draw.textlength(tag, font=f_tag)
    tx, ty = (W - tw) / 2, bar_y + 28
    draw.rounded_rectangle(
        [tx - 32, ty - 10, tx + tw + 32, ty + 62],
        radius=30,
        fill=(0, 0, 0, 210),
        outline=CYAN,
        width=5,
    )
    stroke_text(draw, (tx, ty), tag, f_tag, WHITE, BLACK, width=3)

    # ONE logo only (already says AUGNIIK). Do NOT also paste lobby character circle —
    # when the hero art already shows the character that creates a "double avatar".
    logo = make_avatar_badge(LOGO, size=150, border=9, face_bias=False)
    img.paste(logo, (28, H - logo.size[1] - 40), logo)

    img.convert("RGB").save(out_jpg, quality=97)
    print("shorts ->", out_jpg, flush=True)


def build_yt(bg_path: Path, out_jpg: Path, count: int, subtitle: str) -> None:
    W, H = 1280, 720
    action = Image.open(bg_path).convert("RGB")
    action = ImageEnhance.Contrast(action).enhance(1.55)
    action = ImageEnhance.Color(action).enhance(1.45)
    # mid band of portrait
    band = action.crop((0, 520, 1080, 520 + 720)).resize((900, 720), Image.Resampling.LANCZOS)

    yt = Image.new("RGB", (W, H), (6, 8, 14))
    yt.paste(band, (380, 0))
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    for x in range(0, 560):
        a = int(235 * (1 - x / 560) ** 0.65)
        pd.line([(x, 0), (x, H)], fill=(0, 0, 0, a))
    pd.polygon([(500, 0), (580, 0), (430, H), (350, H)], fill=(0, 0, 0, 130))
    pd.line([(520, 0), (390, H)], fill=(*CYAN, 210), width=7)
    pd.line([(560, 0), (430, H)], fill=(*ORANGE, 210), width=7)
    # side rails
    pd.rectangle([0, 0, 14, H], fill=(*CYAN, 220))
    pd.rectangle([W - 14, 0, W, H], fill=(*ORANGE, 220))

    yt = Image.alpha_composite(yt.convert("RGBA"), panel)
    yd = ImageDraw.Draw(yt)
    f1 = load_font(170)
    f2 = load_font(56)
    f3 = load_font(38)
    num = str(count)
    stroke_text(yd, (36, 120), num, f1, WHITE, BLACK, width=12)
    stroke_text(yd, (44, 310), subtitle, f2, YELLOW, BLACK, width=7)
    stroke_text(yd, (44, 390), "PUBG SHORTS", f3, CYAN, BLACK, width=3)

    # ONE logo only — no lobby character circle (avoids double avatar with hero art)
    logo = make_avatar_badge(LOGO, size=110, border=7, face_bias=False)
    yt.paste(logo, (28, H - logo.size[1] - 24), logo)

    yt.convert("RGB").save(out_jpg, quality=97)
    print("yt ->", out_jpg, flush=True)


def main() -> int:
    if not AVATAR.exists():
        print("missing avatar", AVATAR)
        return 1

    # args: [det_json] [out_dir] [count] [subtitle]
    det = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "work/today/ks1/detection_ks1a.json"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out/30 Kills Assists Today PUBG"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    subtitle = sys.argv[4] if len(sys.argv) > 4 else "KILLS + ASSISTS"

    out_dir.mkdir(parents=True, exist_ok=True)
    work = ROOT / "work/today/ks1/thumb_brand"
    bg = pick_action_bg(det, work)

    # filenames from folder convention
    stem = out_dir.name
    build_shorts(bg, out_dir / f"{stem}.jpg", count, subtitle)
    build_yt(bg, out_dir / f"{stem} YT.jpg", count, subtitle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
