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
from engine.thumb import cover_resize


def sanitize_title(title: str) -> str:
    t = title.replace("|", " ")
    t = re.sub(r'[<>:"/\\?*]', " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


# PUBG ADS / magnified scope HUD — "Zeroing Distance" sits on tip while scoped.
_SCOPE_ZEROING = re.compile(
    r"zeroing|zeroling|zer0ing|distano\s*\d|distance\s*\d{2,3}\s*m",
    re.I,
)


def _load_tip_ocr_for_video(cfg: AppConfig, video: str, index: int) -> dict[int, str]:
    """Reuse detect tip OCR cache to flag scoped kills (no new OCR)."""
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", Path(video).stem)
    work = cfg.work_dir / cfg.game_id / cfg.template_id / f"v{index}_{stem}"
    path = work / "tip_ocr.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[int, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(k)] = str(v or "")
            except ValueError:
                continue
    return out


def _event_scoped(e: dict, tip_ocr: dict[int, str] | None) -> bool:
    """True when scope/ADS was open near the frag (Zeroing Distance tip or event text)."""
    blob = e.get("text") or ""
    if _SCOPE_ZEROING.search(blob):
        return True
    if not tip_ocr:
        return False
    t = int(round(float(e["t"])))
    for dt in range(-3, 4):
        if _SCOPE_ZEROING.search(tip_ocr.get(t + dt) or ""):
            return True
    return False


def _score_cold_event(e: dict, neighbors: list[dict], *, scoped: bool = False) -> float:
    """Rank punchy kills for cold open — kill > knock; headshot/frag/sniper/cluster boost."""
    kind = e.get("kind", "")
    if kind == "my_kill":
        score = 10.0
    elif kind == "my_knock":
        score = 6.0
    else:
        return -1.0
    text = (e.get("text") or "").lower()
    if re.search(r"head\s*shot|dshot|h\$d", text):
        score += 3.5
    if re.search(r"frag|grenade|molotov", text):
        score += 2.5
    if re.search(r"kar98|awm|m24|mk14|aug|mp5", text):
        score += 2.0
    t = float(e["t"])
    near = sum(
        1
        for n in neighbors
        if n.get("kind") in ("my_kill", "my_knock")
        and abs(float(n["t"]) - t) <= 8.0
        and n is not e
    )
    score += min(4.0, near * 1.5)
    if scoped:
        score += 100.0  # hard prefer ADS/scope frags for cold open
    return score


def _pick_spread(
    pool: list[tuple[float, dict, str, float, bool]],
    n: int,
    used_t: list[float],
    *,
    min_gap: float = 12.0,
) -> list[tuple[float, dict, str, float, bool]]:
    """Take top-scored items spread across timeline."""
    picked: list[tuple[float, dict, str, float, bool]] = []
    for item in pool:
        if len(picked) >= n:
            break
        t = float(item[1]["t"])
        if any(abs(t - u) < min_gap for u in used_t):
            continue
        picked.append(item)
        used_t.append(t)
    if len(picked) < n:
        for item in pool:
            if len(picked) >= n:
                break
            if item in picked:
                continue
            picked.append(item)
            used_t.append(float(item[1]["t"]))
    return picked


def build_cold_open(results: list[dict], cfg: AppConfig) -> list[dict]:
    """Top N scoped (ADS) kills as 1s micro-cuts. Scoped only — fewer than N is fine."""
    co = cfg.template.get("cold_open") or {}
    if not co.get("enabled", False):
        return []
    n = int(co.get("count") or 5)
    slice_dur = float(co.get("seconds_each") or 1.0)
    prefer = set(co.get("prefer_kinds") or ["my_kill", "my_knock"])
    # Hard rule: cold open is scoped-only (never pad with hipfire / non-ADS).
    scoped_only = bool(co.get("scoped_only", co.get("prefer_scoped", True)))

    scored: list[tuple[float, dict, str, float, bool]] = []
    for r in results:
        evs = r.get("events") or []
        duration = float(r.get("duration") or 0)
        video = r.get("video") or ""
        idx = int(r.get("index") or 1)
        tip_ocr = _load_tip_ocr_for_video(cfg, video, idx) if scoped_only else {}
        for e in evs:
            if e.get("kind") not in prefer:
                continue
            scoped = _event_scoped(e, tip_ocr) if scoped_only else True
            if scoped_only and not scoped:
                continue
            s = _score_cold_event(e, evs, scoped=scoped)
            if s < 0:
                continue
            scored.append((s, e, video, duration, scoped))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)

    used_t: list[float] = []
    picked = _pick_spread(scored, n, used_t)[:n]

    cold: list[dict] = []
    for s, e, video, duration, scoped in picked:
        t = float(e["t"])
        # Punch: tip/feed flash → impact (~0.2s before banner)
        start = max(0.0, t - 0.2)
        end = min(duration, start + slice_dur)
        if end - start < slice_dur * 0.85 and t + slice_dur <= duration:
            start = max(0.0, t)
            end = min(duration, start + slice_dur)
        cold.append({
            "start": start,
            "end": end,
            "anchor": t,
            "kind": e["kind"],
            "text": e.get("text", ""),
            "dur": round(end - start, 2),
            "video": video,
            "role": "cold_open",
            "score": round(s, 2),
            "scoped": scoped,
        })
    return cold


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
            "role": "body",
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


def plan_clips(results: list[dict], cfg: AppConfig) -> list[dict]:
    """Detect results → ordered clip plan (cold open + body), MH1 length pack applied."""
    progress.stage("assemble", pct=0, message="building clips")
    body_clips: list[dict] = []
    for r in results:
        clips = events_to_clips(r.get("events") or [], float(r.get("duration") or 0), cfg)
        for c in clips:
            c["video"] = r["video"]
        body_clips.extend(clips)

    if not body_clips:
        raise RuntimeError("No clips after detect — check username/clan and kill feed region")

    cold = build_cold_open(results, cfg)
    if cold:
        progress.stage(
            "assemble",
            message=f"cold open {len(cold)}×{cold[0]['dur']:.1f}s",
        )

    # Optional length pack (MH1). kill_short leaves max_total_seconds null — never drop frags.
    # Cold open sits outside the pack budget so the teaser always ships.
    max_total = cfg.template.get("max_total_seconds")
    if max_total is not None and float(max_total) > 0:
        kept = []
        acc = 0.0
        for c in body_clips:
            if acc + c["dur"] > float(max_total) and kept:
                break
            kept.append(c)
            acc += c["dur"]
        body_clips = kept

    return list(cold) + body_clips


def cut_clips(results: list[dict], cfg: AppConfig) -> Path:
    """Encode every planned clip once (final quality) + write a review manifest.

    Assemble later concatenates the chosen subset without re-encoding.
    """
    all_clips = plan_clips(results, cfg)
    clips_dir = cfg.clips_dir
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True)
    aspect = (cfg.template.get("format") or {}).get("aspect_ratio")

    entries: list[dict] = []
    for i, c in enumerate(all_clips, 1):
        outp = clips_dir / f"clip_{i:03d}.mp4"
        role = c.get("role") or "body"
        progress.stage(
            "encode",
            pct=100.0 * (i - 1) / len(all_clips),
            message=f"clip {i}/{len(all_clips)} ({role})",
        )
        encode_segment(Path(c["video"]), c["start"], c["end"], outp, cfg)
        if not outp.exists():
            continue
        entries.append({
            "id": f"c{i:03d}",
            "index": i,
            "path": str(outp),
            "start": round(float(c["start"]), 2),
            "end": round(float(c["end"]), 2),
            "dur": float(c["dur"]),
            "anchor": float(c.get("anchor", c["start"])),
            "kind": c.get("kind", ""),
            "role": role,
            "video": c["video"],
            "text": (c.get("text") or "")[:240],
            "scoped": bool(c.get("scoped")),
            "aspect": aspect,
        })

    if not entries:
        raise RuntimeError("Encode produced no clips")

    manifest = {
        "game": cfg.game_id,
        "template": cfg.template_id,
        "sources": [r.get("video") for r in results],
        "total_events": sum(len(r.get("events") or []) for r in results),
        "clips": entries,
    }
    path = clips_dir / "clips.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    progress.emit(stage="clips", message=f"{len(entries)} clips ready", clips=entries, manifest=str(path))
    return path


def _clip_counts(clips: list[dict]) -> dict[str, int]:
    counts = {"my_knock": 0, "my_kill": 0, "my_assist": 0}
    for c in clips:
        if c.get("role") == "cold_open":
            continue
        for k in str(c.get("kind") or "").split("+"):
            if k in counts:
                counts[k] += 1
    return counts


_WEAPONS = re.compile(
    r"\b(kar98k?|awm|m24|mk14|mini14|slr|sks|aug|m416|akm|beryl|groza|ump|vector|mp5k?|uzi|s686|s12k|dp-?28|m249|frag|grenade|molotov|crossbow|pan)\b",
    re.I,
)


def _ai_copy_ctx(cfg: AppConfig, clips: list[dict], counts: dict) -> dict:
    weapons = sorted({m.group(1).upper() for c in clips for m in _WEAPONS.finditer(c.get("text") or "")})
    extra = ["#Shorts"] if (cfg.template.get("format") or {}).get("aspect_ratio") == "9:16" else ["#MatchHighlight"]
    if counts.get("my_assist"):
        extra += ["#Assist", "#TeamPlay"]
    return {
        "game": cfg.game.get("name") or cfg.game_id,
        "template": cfg.template.get("name") or cfg.template_id,
        "format": (cfg.template.get("format") or {}).get("aspect_ratio"),
        "title_suffix": (cfg.template.get("title_suffix") or "| PUBG").strip(),
        "player": cfg.username,
        "moments": counts,
        "clip_count": len(clips),
        "length_seconds": round(sum(float(c["dur"]) for c in clips), 1),
        "weapons_seen": weapons,
        "multi_kill_clips": sum(1 for c in clips if str(c.get("kind", "")).count("+") >= 1),
        "extra_hashtags": extra,
        "win_confirmed": False,
    }


def _ai_thumbs(cfg: AppConfig, provider: dict, title: str, out_dir: Path, safe: str) -> bool:
    """AI hero base per aspect (never stretched) + one logo composite. True when primary written."""
    from engine import ai
    from engine.thumb import composite, hook_from_title

    character = cfg.brand_path("character") or (cfg.root / "augniik-character.png")
    cover = cfg.brand_path("cover") or (cfg.root / "augniik-cover.jpg")
    logo = cfg.brand_path("profile") or (cfg.root / "augniik-profile.jpg")
    refs = [p for p in (character, cover) if p.exists()]
    hook = hook_from_title(title)
    portrait = (cfg.template.get("format") or {}).get("aspect_ratio") == "9:16"
    jobs = (
        [("9:16", f"{safe}.jpg", (1080, 1920), "PUBG SHORTS"), ("16:9", f"{safe} YT.jpg", (1280, 720), "PUBG")]
        if portrait
        else [("16:9", f"{safe}.jpg", (1920, 1080), "PUBG"), ("16:9", f"{safe} YT.jpg", (1280, 720), "PUBG")]
    )
    bases: dict[str, Path] = {}
    wrote_primary = False
    for i, (aspect, name, size, tag) in enumerate(jobs):
        base = bases.get(aspect)
        if base is None:
            progress.stage("ai", message=f"thumbnail {aspect} via {provider.get('name')}")
            try:
                img = ai.generate_image(
                    provider,
                    ai.thumb_prompt(hook, aspect, character.exists(), cover.exists()),
                    refs,
                    aspect,
                )
            except ai.AiError as e:
                progress.stage("ai", message=f"WARNING thumbnail {aspect} failed: {e}")
                continue
            base = out_dir / f"_ai_base_{aspect.replace(':', 'x')}.png"
            base.write_bytes(img)
            bases[aspect] = base
        composite(base, out_dir / name, hook, size, logo if logo.exists() else None, tag=tag)
        if i == 0:
            wrote_primary = True
    for base in bases.values():
        base.unlink(missing_ok=True)
    return wrote_primary


def package(clips: list[dict], cfg: AppConfig, *, sources: list[str], total_events: int) -> Path:
    """Concat encoded clips (stream copy) → out/{title}/ mp4 + txt + jpg + meta.json."""
    seg_paths = [Path(c["path"]) for c in clips if Path(c["path"]).exists()]
    if not seg_paths:
        raise RuntimeError("No encoded clips to assemble")
    counts = _clip_counts(clips)
    body_count = sum(1 for c in clips if c.get("role") != "cold_open")
    cold_count = len(clips) - body_count

    ai_cfg = cfg.ai or {}
    provider = ai_cfg.get("provider") or None
    ai_used: dict[str, bool] = {}

    title = cfg.title or ""
    description = ""
    if provider and (ai_cfg.get("title") or ai_cfg.get("description")):
        from engine import ai

        progress.stage("ai", message=f"title + description via {provider.get('name')}")
        try:
            copy = ai.generate_copy(provider, _ai_copy_ctx(cfg, clips, counts))
            if ai_cfg.get("title") and not title and copy["title"]:
                title = copy["title"]
                ai_used["title"] = True
            if ai_cfg.get("description") and copy["description"]:
                description = copy["description"]
                ai_used["description"] = True
        except ai.AiError as e:
            progress.stage("ai", message=f"WARNING copy failed: {e}")
    title = title or _default_title(cfg, counts)

    safe = sanitize_title(title)
    out_dir = cfg.output_dir / safe
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{safe}.mp4"
    progress.stage("concat", message=str(mp4))
    concat_copy(seg_paths, mp4)

    jpg = out_dir / f"{safe}.jpg"
    yt = out_dir / f"{safe} YT.jpg"
    if provider and ai_cfg.get("thumbnail"):
        ai_used["thumbnail"] = _ai_thumbs(cfg, provider, title, out_dir, safe)
    if not ai_used.get("thumbnail"):
        _gameplay_thumb(clips, cfg, jpg, yt, title, counts)

    txt = out_dir / f"{safe}.txt"
    if description:
        if not description.startswith(title):
            description = f"{title}\n\n{description}"
        txt.write_text(description.strip() + "\n", encoding="utf-8")
    else:
        write_description(txt, title, cfg, counts, body_count, total_events)

    meta = {
        "title": title,
        "game": cfg.game_id,
        "template": cfg.template_id,
        "aspect": (cfg.template.get("format") or {}).get("aspect_ratio"),
        "resolution": (cfg.template.get("format") or {}).get("resolution"),
        "counts": counts,
        "duration_seconds": round(sum(float(c["dur"]) for c in clips), 2),
        "clip_count": len(clips),
        "cold_open_clips": cold_count,
        "cold_open_scoped": sum(1 for c in clips if c.get("role") == "cold_open" and c.get("scoped")),
        "body_clips": body_count,
        "total_events": total_events,
        "sources": sources,
        "username": cfg.username,
        "thumbnail": jpg.name,
        "thumbnail_youtube": yt.name,
        **({"ai_provider": provider.get("name"), "ai_used": ai_used} if provider else {}),
    }
    meta_path = out_dir / f"{safe}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    progress.artifact(str(mp4), kind="video")
    progress.artifact(str(out_dir), kind="package")
    progress.stage("assemble", pct=100, message="done")
    return out_dir


def assemble_selected(cfg: AppConfig, manifest_path: Path, selected: list[str] | None) -> Path:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    clips = data.get("clips") or []
    if selected is not None:
        keep = set(selected)
        clips = [c for c in clips if c.get("id") in keep]
    if not clips:
        raise RuntimeError("No clips selected")
    progress.stage("assemble", message=f"assembling {len(clips)} selected clips")
    return package(
        clips,
        cfg,
        sources=list(data.get("sources") or []),
        total_events=int(data.get("total_events") or 0),
    )


def assemble(results: list[dict], cfg: AppConfig) -> Path:
    manifest = cut_clips(results, cfg)
    return assemble_selected(cfg, manifest, None)


def _gameplay_thumb(all_clips: list[dict], cfg: AppConfig, jpg: Path, yt: Path, title: str, counts: dict) -> None:
    out_dir = jpg.parent
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
            cover_resize(im, 1280, 720).save(yt, quality=95)
            tmp.unlink(missing_ok=True)
        else:
            _fallback_thumb(jpg, yt, title, counts)
    except Exception:
        _fallback_thumb(jpg, yt, title, counts)
