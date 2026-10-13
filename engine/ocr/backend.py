"""Single OCR entry: RapidOCR (bundled ONNX) → Windows.Media.Ocr → empty."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_BACKEND: str | None = None
_ENGINE = None


def _resources() -> Path:
    env = os.environ.get("HIGHLIGHT_RESOURCES")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _try_rapidocr():
    global _ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        try:
            from rapidocr import RapidOCR  # type: ignore
        except ImportError:
            return None
    models = _resources() / "models" / "rapidocr"
    kwargs: dict = {}
    # If bundled models exist, point at them; else RapidOCR downloads/defaults
    det = models / "ch_PP-OCRv4_det_infer.onnx"
    rec = models / "ch_PP-OCRv4_rec_infer.onnx"
    cls = models / "ch_ppocr_mobile_v2.0_cls_infer.onnx"
    if det.exists() and rec.exists():
        kwargs["det_model_path"] = str(det)
        kwargs["rec_model_path"] = str(rec)
        if cls.exists():
            kwargs["cls_model_path"] = str(cls)
    try:
        _ENGINE = RapidOCR(**kwargs) if kwargs else RapidOCR()
        return "rapidocr"
    except Exception:
        _ENGINE = None
        return None


def _windows_ocr(path: Path) -> str:
    scripts = _resources() / "scripts" / "ocr_frame.ps1"
    if not scripts.exists() or os.name != "nt":
        return ""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(scripts), "-ImagePath", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (r.stdout or "").strip()


def get_backend_name() -> str:
    global _BACKEND
    if _BACKEND is None:
        name = _try_rapidocr()
        if name:
            _BACKEND = name
        elif os.name == "nt":
            _BACKEND = "windows_ocr"
        else:
            _BACKEND = "none"
    return _BACKEND


def _preprocess(path: Path) -> Path:
    """Contrast + 2x upscale — helps kill-feed glyphs on busy backgrounds."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError:
        return path
    try:
        im = Image.open(path).convert("L")
        im = ImageOps.autocontrast(im)
        im = ImageEnhance.Contrast(im).enhance(1.8)
        im = im.resize((im.width * 2, im.height * 2), Image.Resampling.LANCZOS)
        out = path.with_name(path.stem + "_ocrprep.jpg")
        im.convert("RGB").save(out, quality=95)
        return out
    except Exception:
        return path


def ocr_crop(path: Path | str) -> str:
    """OCR a single already-cropped image. Sparse use only."""
    p = Path(path)
    if not p.exists():
        return ""
    prep = _preprocess(p)
    backend = get_backend_name()
    text = ""
    if backend == "rapidocr" and _ENGINE is not None:
        try:
            result, _ = _ENGINE(str(prep))
            if result:
                lines = [row[1] for row in result if row and len(row) > 1]
                text = " ".join(lines)
        except Exception as exc:
            print(f"rapidocr fail: {exc}", file=sys.stderr, flush=True)
    if not text and os.name == "nt":
        text = _windows_ocr(prep) or _windows_ocr(p)
    if prep != p:
        prep.unlink(missing_ok=True)
    return (text or "").strip()
