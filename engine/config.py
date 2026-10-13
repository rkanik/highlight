"""Load and merge game/template config: defaults → user → job override."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def _package_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


ROOT = _package_root()
DEFAULTS_PATH = ROOT / "config" / "defaults.yaml"


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    # Minimal fallback without PyYAML: refuse complex files
    raise RuntimeError(f"PyYAML required to load {path}")


def _source_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def user_config_path() -> Path:
    env = os.environ.get("HIGHLIGHT_USER_CONFIG")
    if env:
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
        return base / "HighlightApp" / "config.yaml"
    return Path.home() / ".highlight" / "config.yaml"


def resources_dir() -> Path:
    env = os.environ.get("HIGHLIGHT_RESOURCES")
    if env:
        return Path(env)
    return ROOT


def ffmpeg_bin() -> str:
    return os.environ.get("HIGHLIGHT_FFMPEG", "ffmpeg")


def ffprobe_bin() -> str:
    return os.environ.get("HIGHLIGHT_FFPROBE", "ffprobe")


def load_raw(
    defaults_path: Path | None = None,
    user_path: Path | None = None,
    job: dict | None = None,
) -> dict:
    defaults = _load_yaml(defaults_path or DEFAULTS_PATH)
    user = {}
    up = user_path or user_config_path()
    if up.suffix.lower() == ".json":
        user = _load_json(up)
    elif up.exists():
        user = _load_yaml(up)
    merged = _deep_merge(defaults, user)
    if job:
        merged = _deep_merge(merged, job)
    return merged


class AppConfig:
    """Resolved view of active game + template + paths + device."""

    def __init__(self, raw: dict, root: Path | None = None):
        self.raw = raw
        self.root = root or resources_dir()
        self.game_id = str(raw.get("active_game") or "pubg")
        self.template_id = str(raw.get("active_template") or "kill_short")
        games = raw.get("games") or {}
        if self.game_id not in games:
            raise ValueError(f"Unknown game: {self.game_id}")
        self.game = games[self.game_id]
        templates = self.game.get("templates") or {}
        if self.template_id not in templates:
            raise ValueError(f"Unknown template {self.template_id} for game {self.game_id}")
        self.template = templates[self.template_id]
        self.paths = raw.get("paths") or {}
        self.device = raw.get("device") or {}
        self.safety = raw.get("safety") or {}
        self.track = self.game.get("track") or {}
        self.regions = self.game.get("regions") or {}
        self.detection = self.game.get("detection") or {}
        # Job-level source list: plain paths or {"path", "scan_ranges": [[s, e], ...]}
        self.sources: list[str] = []
        self._source_ranges: dict[str, list[tuple[float, float]]] = {}
        for item in raw.get("sources") or []:
            if isinstance(item, dict):
                path = str(item.get("path") or "")
                ranges = item.get("scan_ranges") or []
            else:
                path, ranges = str(item), []
            if not path:
                continue
            self.sources.append(path)
            parsed = [
                (float(r[0]), float(r[1]))
                for r in ranges
                if isinstance(r, (list, tuple)) and len(r) == 2
            ]
            if parsed:
                self._source_ranges[_source_key(path)] = parsed
        self.title: str | None = raw.get("title")
        self.ai_base: str | None = raw.get("ai_base")
        self.ai: dict = raw.get("ai") or {}

    @property
    def username(self) -> str:
        return str(self.track.get("username") or "").strip()

    @property
    def clan_tags(self) -> list[str]:
        return list(self.track.get("clan_tags") or [])

    @property
    def name_stems(self) -> list[str]:
        stems = list(self.track.get("name_stems") or [])
        if self.username and self.username.lower() not in [s.lower() for s in stems]:
            stems.insert(0, self.username.lower())
        return stems

    @property
    def include(self) -> set[str]:
        return set(self.template.get("include") or [])

    @property
    def detection_mode(self) -> str:
        return str(self.detection.get("mode") or "feed_ocr")

    @property
    def work_dir(self) -> Path:
        p = Path(self.paths.get("work_dir") or "work")
        return p if p.is_absolute() else self.root / p

    @property
    def output_dir(self) -> Path:
        p = Path(self.paths.get("output_dir") or "out")
        return p if p.is_absolute() else self.root / p

    def absolute_box(self, region_name: str, width: int, height: int) -> tuple[int, int, int, int]:
        """Convert relative region to absolute (x1,y1,x2,y2)."""
        r = self.regions.get(region_name) or {}
        # tip may be enabled via template regions even if game.regions.tip.enabled is false
        if r.get("enabled") is False and region_name not in ("kill_feed", "tip"):
            raise KeyError(f"Region disabled: {region_name}")
        x = float(r.get("x", 0)) * width
        y = float(r.get("y", 0)) * height
        w = float(r.get("w", 0)) * width
        h = float(r.get("h", 0)) * height
        x1, y1 = int(round(x)), int(round(y))
        x2, y2 = int(round(x + w)), int(round(y + h))
        x1 = max(0, min(x1, width - 1))
        y1 = max(0, min(y1, height - 1))
        x2 = max(x1 + 1, min(x2, width))
        y2 = max(y1 + 1, min(y2, height))
        return x1, y1, x2, y2

    def template_regions(self) -> list[str]:
        return list(self.template.get("regions") or ["kill_feed"])

    def scan_window(self, duration: float) -> tuple[float, float]:
        """Resolve optional scan trim to [start, end] seconds within the VOD."""
        dur = max(0.0, float(duration))
        raw_start = self.detection.get("scan_start_seconds")
        raw_end = self.detection.get("scan_end_seconds")
        t0 = float(raw_start) if raw_start is not None else 0.0
        t1 = float(raw_end) if raw_end is not None else dur
        if dur <= 0:
            return 0.0, 0.0
        t0 = max(0.0, min(t0, dur))
        t1 = max(t0, min(t1, dur))
        if t1 - t0 < 1.0:
            t1 = min(dur, t0 + 1.0)
        return t0, t1

    def scan_ranges(self, video: str | Path, duration: float) -> list[tuple[float, float]]:
        """Per-source scan windows (clamped + merged); falls back to the global scan window."""
        ranges = self._source_ranges.get(_source_key(str(video)))
        if not ranges:
            return [self.scan_window(duration)]
        dur = max(0.0, float(duration))
        clamped = sorted(
            (max(0.0, min(a, dur)), max(0.0, min(b, dur))) for a, b in ranges
        )
        merged: list[tuple[float, float]] = []
        for a, b in clamped:
            if b - a < 1.0:
                continue
            if merged and a <= merged[-1][1] + 1.0:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged or [self.scan_window(duration)]

    def brand_path(self, kind: str) -> Path | None:
        """Resolve brand asset (character / cover / profile); relative paths are repo-root based."""
        raw = (self.paths.get("brand") or {}).get(kind)
        if not raw:
            return None
        p = Path(str(raw))
        p = p if p.is_absolute() else self.root / p
        return p if p.exists() else None

    @property
    def clips_dir(self) -> Path:
        return self.work_dir / self.game_id / self.template_id / "clips"

    def as_job_snapshot(self) -> dict[str, Any]:
        return {
            "game": self.game_id,
            "template": self.template_id,
            "username": self.username,
            "include": sorted(self.include),
            "detection_mode": self.detection_mode,
            "sources": self.sources,
            "title": self.title,
            "scan_start_seconds": self.detection.get("scan_start_seconds"),
            "scan_end_seconds": self.detection.get("scan_end_seconds"),
            "scan_ranges": {k: v for k, v in self._source_ranges.items()},
            "ai_provider": (self.ai.get("provider") or {}).get("name"),
        }


def load_config(
    job_path: Path | None = None,
    job: dict | None = None,
    defaults_path: Path | None = None,
) -> AppConfig:
    job_data = job or {}
    if job_path:
        job_data = _deep_merge(job_data, _load_json(job_path) if job_path.suffix == ".json" else _load_yaml(job_path))
    # Allow job to set active_game / active_template shortcuts
    if "game" in job_data and "active_game" not in job_data:
        job_data["active_game"] = job_data["game"]
    if "template" in job_data and "active_template" not in job_data:
        job_data["active_template"] = job_data["template"]
    raw = load_raw(defaults_path=defaults_path, job=job_data)
    return AppConfig(raw)


def list_games(raw: dict | None = None) -> list[dict]:
    data = raw or load_raw()
    out = []
    for gid, g in (data.get("games") or {}).items():
        out.append({
            "id": gid,
            "name": g.get("name") or gid,
            "templates": [
                {"id": tid, "name": t.get("name") or tid, "description": t.get("description") or ""}
                for tid, t in (g.get("templates") or {}).items()
            ],
        })
    return out
