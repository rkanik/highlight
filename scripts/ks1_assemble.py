#!/usr/bin/env python3
"""Assemble KS1 short from detection_all.json and package deliverables."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path("V:/highlight")
WORK = ROOT / "work" / "today" / "ks1"
OUT = ROOT / "out"

PAD_BEFORE = 4.0
PAD_AFTER = 3.0
MERGE_GAP = 3.0
MIN_CLIP = 5.0
MAX_CLIP = 30.0


def run(cmd, check=True):
    print("+", " ".join(map(str, cmd[:16])), ("..." if len(cmd) > 16 else ""))
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def ffprobe_duration(path: Path) -> float:
    r = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(r.stdout.strip())


def events_to_clips(events, duration):
    raw = []
    for e in events:
        start = max(0.0, e["t"] - PAD_BEFORE)
        end = min(duration, e["t"] + PAD_AFTER)
        raw.append({"start": start, "end": end, "anchor": e["t"], "kind": e["kind"], "text": e.get("text", "")})
    if not raw:
        return []
    raw.sort(key=lambda c: c["start"])
    merged = [raw[0].copy()]
    for c in raw[1:]:
        cur = merged[-1]
        if c["start"] <= cur["end"] + MERGE_GAP:
            cur["end"] = max(cur["end"], c["end"])
            cur["kind"] = cur["kind"] + "+" + c["kind"]
        else:
            merged.append(c.copy())
    out = []
    for c in merged:
        dur = c["end"] - c["start"]
        if dur < MIN_CLIP:
            mid = (c["start"] + c["end"]) / 2
            c["start"] = max(0.0, mid - MIN_CLIP / 2)
            c["end"] = min(duration, c["start"] + MIN_CLIP)
        if c["end"] - c["start"] > MAX_CLIP:
            anchor = c.get("anchor", c["start"] + PAD_BEFORE)
            c["start"] = max(0.0, anchor - PAD_BEFORE)
            c["end"] = min(duration, c["start"] + MAX_CLIP)
        c["dur"] = round(c["end"] - c["start"], 2)
        out.append(c)
    return out


def encode_seg(video: Path, start: float, end: float, out_path: Path) -> None:
    dur = max(0.1, end - start)
    vf = "crop=608:1000:656:40,scale=1080:1920"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-threads", "1",
        "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
        "-vf", vf, "-r", "60",
        "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "18",
        "-b:v", "12M", "-maxrate", "16M", "-bufsize", "24M",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-threads", "1",
        "-movflags", "+faststart", "-y", str(out_path),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not out_path.exists():
        # Still NVENC — never libx264 CPU encode
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda", "-threads", "1",
            "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
            "-vf", vf, "-r", "60",
            "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "18", "-b:v", "12M",
            "-c:a", "aac", "-b:a", "192k", "-threads", "1",
            "-y", str(out_path),
        ]
        run(cmd)


def concat_segs(segs, out_path: Path) -> None:
    lst = out_path.parent / "concat.txt"
    lines = [f"file '{str(s).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for s in segs]
    # ffmpeg concat on Windows prefers forward slashes
    lines = []
    for s in segs:
        p = Path(s).resolve().as_posix()
        lines.append(f"file '{p}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", "-movflags", "+faststart", "-y", str(out_path),
    ])


def sanitize_title(title: str) -> str:
    t = title.replace("|", " ")
    t = re.sub(r'[<>:"/\\?*]', " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def _pick_thumb_source(clips: list[dict], out_dir: Path) -> Path:
    """Grab a peak kill-frame from source (prefer my_kill / late knock), portrait crop."""
    tmp = out_dir / "_thumb_src.jpg"
    if not clips:
        return tmp
    # prefer kills, else longest clip anchor
    ranked = sorted(
        clips,
        key=lambda c: (
            2 if "my_kill" in c.get("kind", "") else 1,
            c.get("dur", 0),
        ),
        reverse=True,
    )
    best = ranked[0]
    # slightly after knock/kill banner pops
    ss = float(best.get("anchor", best["start"] + 4.0)) + 0.4
    vf = "crop=608:1000:656:40,scale=1080:1920"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-threads", "1",
        "-ss", f"{ss:.2f}", "-i", str(best["video"]),
        "-frames:v", "1", "-vf", vf, "-threads", "1",
        "-y", str(tmp),
    ], check=False)
    if not tmp.exists():
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda", "-threads", "1",
            "-ss", f"{ss:.2f}", "-i", str(best["video"]),
            "-frames:v", "1", "-vf", vf, "-y", str(tmp),
        ])
    return tmp


def make_thumbnail(
    clips: list[dict],
    title: str,
    out_jpg: Path,
    out_yt: Path,
    kill_count: int,
    hook: str = "KILL SHOTS",
    badge_label: str | None = None,
) -> None:
    tmp = _pick_thumb_source(clips, out_jpg.parent)
    im = Image.open(tmp).convert("RGB")
    # punchy grade
    im = ImageEnhance.Contrast(im).enhance(1.45)
    im = ImageEnhance.Color(im).enhance(1.35)
    im = ImageEnhance.Sharpness(im).enhance(1.25)
    # slight zoom for intensity
    w, h = im.size
    zw, zh = int(w * 0.92), int(h * 0.92)
    im = im.crop(((w - zw) // 2, (h - zh) // 5, (w + zw) // 2, (h + zh) // 5 + zh)).resize((w, h), Image.Resampling.LANCZOS)

    # bottom gradient for clean text plate
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    g0 = int(h * 0.55)
    for y in range(g0, h):
        t = (y - g0) / max(1, h - g0)
        a = int(210 * (t ** 1.2))
        od.line([(0, y), (w, y)], fill=(0, 0, 0, min(230, a)))
    # soft vignette
    vign = Image.new("L", im.size, 0)
    vd = ImageDraw.Draw(vign)
    vd.ellipse((-w * 0.15, -h * 0.1, w * 1.15, h * 1.1), fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(90))
    base = Image.composite(im, Image.new("RGB", im.size, (0, 0, 0)), vign).convert("RGBA")
    im = Image.alpha_composite(base, overlay).convert("RGB")

    draw = ImageDraw.Draw(im)
    font_paths = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
    ]
    font_big = font_mid = font_small = ImageFont.load_default()
    for fp in font_paths:
        try:
            font_big = ImageFont.truetype(fp, 110)
            font_mid = ImageFont.truetype(fp, 78)
            font_small = ImageFont.truetype(fp, 54)
            break
        except OSError:
            continue

    def stroke_text(xy, text, font, fill, stroke, width=6):
        x, y = xy
        for dx in range(-width, width + 1, 2):
            for dy in range(-width, width + 1, 2):
                if dx or dy:
                    draw.text((x + dx, y + dy), text, font=font, fill=stroke)
        draw.text((x, y), text, font=font, fill=fill)

    # BIG count badge
    badge = badge_label or f"{kill_count} KILLS"
    tw = draw.textlength(badge, font=font_big)
    bx = (w - tw) / 2
    by = int(h * 0.58)
    # red plate behind badge
    pad_x, pad_y = 28, 10
    draw.rounded_rectangle(
        [bx - pad_x, by - pad_y, bx + tw + pad_x, by + 110 + pad_y],
        radius=18,
        fill="#C40000",
        outline="#FFEE00",
        width=5,
    )
    stroke_text((bx, by), badge, font_big, "#FFFFFF", "#000000", width=4)

    tw = draw.textlength(hook, font=font_mid)
    stroke_text(((w - tw) / 2, by + 130), hook, font_mid, "#FFEE00", "#000000", width=5)

    tw = draw.textlength("PUBG", font=font_small)
    stroke_text(((w - tw) / 2, by + 220), "PUBG", font_small, "#FFFFFF", "#000000", width=4)

    im.save(out_jpg, quality=95)
    # YT 1280x720 from center band
    yt = im.resize((1280, int(1280 * h / w))).crop((0, 180, 1280, 900)).resize((1280, 720))
    # reinforce text on YT variant
    yd = ImageDraw.Draw(yt)
    try:
        yf = ImageFont.truetype(font_paths[0], 86)
        yf2 = ImageFont.truetype(font_paths[0], 48)
    except OSError:
        yf = font_mid
        yf2 = font_small
    yt_sub = f"{hook} | PUBG"
    for dx, dy in [(-4, 0), (4, 0), (0, -4), (0, 4)]:
        yd.text((40 + dx, 560 + dy), badge, font=yf, fill="#000000")
        yd.text((40 + dx, 650 + dy), yt_sub, font=yf2, fill="#000000")
    yd.text((40, 560), badge, font=yf, fill="#FFEE00")
    yd.text((40, 650), yt_sub, font=yf2, fill="#FFFFFF")
    yt.save(out_yt, quality=95)
    tmp.unlink(missing_ok=True)


def write_description(
    path: Path,
    title: str,
    total_events: int,
    clip_count: int,
    template: str,
    counts: dict,
) -> None:
    if template == "KS1A":
        body = f"""{title}

PUBG kill + assist montage (KS1A) — knocks, kills, and my assist credits from today's matches. Spectator segments skipped. No Chicken Dinner end-card.

Moments:
- {counts.get('my_knock', 0)} knocks · {counts.get('my_kill', 0)} kills · {counts.get('my_assist', 0)} assists
- {total_events} events across {clip_count} fight clips
- Player POV only ([ASA] rkanik)

Keywords:
PUBG highlights, PUBG kills, PUBG assist, PUBG knock, kill montage, team fight, PUBG TPP, battlegrounds highlights, FPS short, gaming reel, multi kill

#PUBG #PUBGHighlights #KillMontage #Assist #TeamPlay #FPS #Gaming #Shorts #Reels #Battlegrounds #PCGaming #Twitch #ActionGames #Gunfight #PUBGPC
"""
    else:
        body = f"""{title}

PUBG kill-shot montage (KS1) — only knocks and kills from today's matches. Spectator segments skipped. No Chicken Dinner end-card.

Moments:
- {total_events} knock/kill events kept across {clip_count} fight clips
- Player POV only ([ASA] rkanik)
- Hard cuts between fights

Keywords:
PUBG highlights, PUBG kills, PUBG knock, kill montage, PUBG TPP, battlegrounds highlights, FPS short, gaming reel, multi kill, squad fight

#PUBG #PUBGHighlights #KillMontage #FPS #Gaming #Shorts #Reels #Battlegrounds #PCGaming #Twitch #ActionGames #Gunfight #PUBGPC
"""
    path.write_text(body.strip() + "\n", encoding="utf-8")


def main() -> int:
    template = "KS1A" if "--ks1a" in sys.argv else "KS1"
    det_path = WORK / ("detection_ks1a.json" if template == "KS1A" else "detection_all.json")
    segs_dir = WORK / ("segs_ks1a" if template == "KS1A" else "segs")

    if not det_path.exists():
        print("missing", det_path)
        return 1
    results = json.loads(det_path.read_text(encoding="utf-8"))

    all_clips = []
    total_events = 0
    counts = {"my_knock": 0, "my_kill": 0, "my_assist": 0}
    for r in results:
        clips = events_to_clips(r["events"], r["duration"])
        for c in clips:
            c["video"] = r["video"]
        all_clips.extend(clips)
        total_events += len(r["events"])
        for e in r["events"]:
            k = e.get("kind")
            if k in counts:
                counts[k] += 1

    clip_json = WORK / ("all_clips_ks1a.json" if template == "KS1A" else "all_clips.json")
    clip_json.write_text(
        json.dumps({"clips": all_clips, "total_events": total_events, "counts": counts, "template": template}, indent=2),
        encoding="utf-8",
    )
    print(f"[{template}] events={total_events} clips={len(all_clips)} counts={counts} seconds={sum(c['dur'] for c in all_clips):.1f}")
    if not all_clips:
        return 2

    if segs_dir.exists():
        shutil.rmtree(segs_dir)
    segs_dir.mkdir(parents=True)
    seg_paths = []
    for i, c in enumerate(all_clips, 1):
        outp = segs_dir / f"seg_{i:03d}.mp4"
        print(f"encode {i}/{len(all_clips)} {Path(c['video']).name} {c['start']:.1f}-{c['end']:.1f}")
        encode_seg(Path(c["video"]), c["start"], c["end"], outp)
        seg_paths.append(outp)

    if template == "KS1A":
        title = f"{total_events} Kills Assists Today | PUBG"
        hook = "KILLS + ASSISTS"
        badge = f"{total_events} PLAYS"
    else:
        title = f"{total_events} Kill Shots Today | PUBG"
        hook = "KILL SHOTS"
        badge = f"{total_events} KILLS"
    folder = sanitize_title(title)
    out_dir = OUT / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    final_mp4 = out_dir / f"{folder}.mp4"
    concat_segs(seg_paths, final_mp4)
    # Always use AUGNIIK branded thumbs (white-border avatar)
    det_for_thumb = det_path
    subtitle = "KILLS + ASSISTS" if template == "KS1A" else "KILL SHOTS"
    thumb_script = ROOT / "scripts" / "make_augniik_thumb.py"
    run([
        sys.executable, "-u", str(thumb_script),
        str(det_for_thumb), str(out_dir), str(total_events), subtitle,
    ], check=False)
    # fallback if brand script fails
    if not (out_dir / f"{folder}.jpg").exists():
        make_thumbnail(
            all_clips, title,
            out_dir / f"{folder}.jpg",
            out_dir / f"{folder} YT.jpg",
            total_events, hook=hook, badge_label=badge,
        )
    write_description(out_dir / f"{folder}.txt", title, total_events, len(all_clips), template, counts)
    meta = {
        "template": template,
        "title": title,
        "folder": folder,
        "video": final_mp4.name,
        "description": f"{folder}.txt",
        "thumbnail": f"{folder}.jpg",
        "thumbnail_youtube": f"{folder} YT.jpg",
        "events": total_events,
        "counts": counts,
        "clips": len(all_clips),
        "duration_seconds": round(ffprobe_duration(final_mp4), 2),
        "sources": [Path(r["video"]).name for r in results],
    }
    (out_dir / f"{folder}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("DONE", out_dir)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
