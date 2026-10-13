"""Assemble filtered events into short package: mp4, txt, jpg, meta.json."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from engine import progress
from engine.config import AppConfig
from engine.config import ffmpeg_bin
from engine.ffmpeg_util import concat_copy, encode_segment, run


def sanitize_title(title: str) -> str:
    t = title.replace("|", " ")
    t = re.sub(r'[<>:"/\\?*]', " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def events_to_clips(events: list[dict], duration: float, cfg: AppConfig) -> list[dict]:
    pad = cfg.template.get("padding") or {}
    merge = cfg.template.get("clip_merging") or {}
    before = float(pad.get("before_seconds") or 4)
    after = float(pad.get("after_seconds") or 3)
    gap = float(merge.get("merge_gap_seconds") or 3)
    min_clip = float(merge.get("min_clip_seconds") or 5)
    max_clip = float(merge.get("max_single_clip_seconds") or 30)

    raw = []
    for e in events:
        start = max(0.0, float(e["t"]) - before)
        end = min(duration, float(e["t"]) + after)
        raw.append({
            "start": start, "end": end, "anchor": float(e["t"]),
            "kind": e["kind"], "text": e.get("text", ""),
        })
    if not raw:
        return []
    raw.sort(key=lambda c: c["start"])
    merged = [raw[0].copy()]
    for c in raw[1:]:
        cur = merged[-1]
        if c["start"] <= cur["end"] + gap:
            cur["end"] = max(cur["end"], c["end"])
            cur["kind"] = cur["kind"] + "+" + c["kind"]
        else:
            merged.append(c.copy())
    out = []
    for c in merged:
        dur = c["end"] - c["start"]
        if dur < min_clip:
            mid = (c["start"] + c["end"]) / 2
            c["start"] = max(0.0, mid - min_clip / 2)
            c["end"] = min(duration, c["start"] + min_clip)
        if c["end"] - c["start"] > max_clip:
            anchor = c.get("anchor", c["start"] + before)
            c["start"] = max(0.0, anchor - before)
            c["end"] = min(duration, c["start"] + max_clip)
        c["dur"] = round(c["end"] - c["start"], 2)
        out.append(c)
    return out


def _default_title(cfg: AppConfig, counts: dict) -> str:
    suffix = (cfg.template.get("title_suffix") or "| PUBG").strip()
    if counts.get("my_assist"):
        hook = "Squad Fight Assist Chain"
    elif counts.get("my_kill", 0) >= 3:
        hook = "Kill Feed Frag Storm"
    elif counts.get("my_knock"):
        hook = "Knock Chain Spree"
    else:
        hook = "Fight Highlight"
    return f"{hook} {suffix}"


def _fallback_thumb(out_jpg: Path, out_yt: Path, title: str, counts: dict) -> None:
    """Simple branded fallback thumb (no giant kill count as hero — hook words)."""
    w, h = 1080, 1920
    im = Image.new("RGB", (w, h), (12, 12, 18))
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/impact.ttf", 72)
        font2 = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 42)
    except OSError:
        font = ImageFont.load_default()
        font2 = font
    hook = title.split("|")[0].strip() if "|" in title else title
    draw.text((60, 800), hook[:28], fill="#FFEE00", font=font)
    draw.text((60, 900), "PUBG", fill="#FFFFFF", font=font2)
    meta = f"{counts.get('my_knock', 0)} knocks · {counts.get('my_kill', 0)} kills · {counts.get('my_assist', 0)} assists"
    draw.text((60, 980), meta, fill="#AAAAAA", font=font2)
    im.save(out_jpg, quality=92)
    yt = im.resize((1280, 720))
    yt.save(out_yt, quality=92)


def write_description(path: Path, title: str, cfg: AppConfig, counts: dict, clip_count: int, total: int) -> None:
    body = f"""{title}

PUBG {cfg.template.get('name') or cfg.template_id} — knocks, kills, and assists from kill feed. Spectator skipped.

Moments:
- {counts.get('my_knock', 0)} knocks · {counts.get('my_kill', 0)} kills · {counts.get('my_assist', 0)} assists
- {total} events across {clip_count} fight clips
- Player POV only ([{'/'.join(cfg.clan_tags) or 'clan'}] {cfg.username})

Keywords:
PUBG highlights, PUBG kills, PUBG assist, PUBG knock, kill montage, team fight, Shorts

#PUBG #PUBGHighlights #KillMontage #Assist #TeamPlay #FPS #Gaming #Shorts
"""
    path.write_text(body.strip() + "\n", encoding="utf-8")


def assemble(results: list[dict], cfg: AppConfig) -> Path:
    progress.stage("assemble", pct=0, message="building clips")
    all_clips: list[dict] = []
    total_events = 0
    counts = {"my_knock": 0, "my_kill": 0, "my_assist": 0}
    for r in results:
        clips = events_to_clips(r.get("events") or [], float(r.get("duration") or 0), cfg)
        for c in clips:
            c["video"] = r["video"]
        all_clips.extend(clips)
        total_events += len(r.get("events") or [])
        for e in r.get("events") or []:
            k = e.get("kind")
            if k in counts:
                counts[k] += 1

    if not all_clips:
        raise RuntimeError("No clips after detect — check username/clan and kill feed region")

    # Optional length pack (MH1). kill_short leaves max_total_seconds null — never drop frags.
    max_total = cfg.template.get("max_total_seconds")
    if max_total is not None and float(max_total) > 0:
        kept = []
        acc = 0.0
        for c in all_clips:
            if acc + c["dur"] > float(max_total) and kept:
                break
            kept.append(c)
            acc += c["dur"]
        all_clips = kept

    segs_dir = cfg.work_dir / cfg.game_id / cfg.template_id / "segs"
    if segs_dir.exists():
        shutil.rmtree(segs_dir)
    segs_dir.mkdir(parents=True)

    seg_paths = []
    for i, c in enumerate(all_clips, 1):
        outp = segs_dir / f"seg_{i:03d}.mp4"
        progress.stage("encode", pct=100.0 * (i - 1) / len(all_clips), message=f"seg {i}/{len(all_clips)}")
        encode_segment(Path(c["video"]), c["start"], c["end"], outp, cfg)
        if outp.exists():
            seg_paths.append(outp)

    if not seg_paths:
        raise RuntimeError("Encode produced no segments")

    title = cfg.title or _default_title(cfg, counts)
    safe = sanitize_title(title)
    out_dir = cfg.output_dir / safe
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{safe}.mp4"
    progress.stage("concat", message=str(mp4))
    concat_copy(seg_paths, mp4)

    jpg = out_dir / f"{safe}.jpg"
    yt = out_dir / f"{safe} YT.jpg"
    # Grab a still for thumb if possible
    try:
        best = sorted(all_clips, key=lambda c: (2 if "my_kill" in c.get("kind", "") else 1, c.get("dur", 0)), reverse=True)[0]
        tmp = out_dir / "_thumb_src.jpg"
        ss = float(best.get("anchor", best["start"])) + 0.4
        fmt = cfg.template.get("format") or {}
        crop = fmt.get("crop")
        if crop:
            vf = f"crop={crop['portrait_width']}:{crop['portrait_height']}:{crop['x_offset']}:{crop['y_offset']},scale=1080:1920"
        else:
            vf = "scale=1920:1080"
        run([
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
            "-ss", f"{ss:.2f}", "-i", str(best["video"]),
            "-frames:v", "1", "-vf", vf, "-y", str(tmp),
        ], check=False)
        if tmp.exists():
            im = Image.open(tmp).convert("RGB")
            im = ImageEnhance.Contrast(im).enhance(1.3)
            im.save(jpg, quality=95)
            im.resize((1280, 720)).save(yt, quality=95)
            tmp.unlink(missing_ok=True)
        else:
            _fallback_thumb(jpg, yt, title, counts)
    except Exception:
        _fallback_thumb(jpg, yt, title, counts)

    txt = out_dir / f"{safe}.txt"
    write_description(txt, title, cfg, counts, len(all_clips), total_events)

    meta = {
        "title": title,
        "game": cfg.game_id,
        "template": cfg.template_id,
        "aspect": (cfg.template.get("format") or {}).get("aspect_ratio"),
        "resolution": (cfg.template.get("format") or {}).get("resolution"),
        "counts": counts,
        "duration_seconds": round(sum(c["dur"] for c in all_clips), 2),
        "clip_count": len(all_clips),
        "total_events": total_events,
        "sources": [r.get("video") for r in results],
        "username": cfg.username,
    }
    meta_path = out_dir / f"{safe}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    progress.artifact(str(mp4), kind="video")
    progress.artifact(str(out_dir), kind="package")
    progress.stage("assemble", pct=100, message="done")
    return out_dir
