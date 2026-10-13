"""Single OCR entry: RapidOCR (ONNX, DirectML/CUDA/CPU) → Windows.Media.Ocr → empty."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_BACKEND: str | None = None
_ENGINE = None
_EP_NAME: str = "cpu"


def _resources() -> Path:
    env = os.environ.get("HIGHLIGHT_RESOURCES")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _patch_ort_gpu_providers() -> str:
    """
    RapidOCR only knows CUDA. On this PC CUDA EP needs CUDA 13 toolkit (missing).
    Prefer DirectML (onnxruntime-directml) → CUDA → CPU.
    """
    try:
        from rapidocr_onnxruntime import utils as ort_utils
        from onnxruntime import (
            GraphOptimizationLevel,
            InferenceSession,
            SessionOptions,
            get_available_providers,
        )
    except ImportError:
        return "cpu"

    if getattr(ort_utils.OrtInferSession, "_highlight_patched", False):
        return _EP_NAME

    providers = list(get_available_providers())
    chosen = "cpu"
    if "DmlExecutionProvider" in providers:
        chosen = "dml"
    elif "CUDAExecutionProvider" in providers:
        chosen = "cuda"

    def _init(self, config):  # noqa: ANN001
        sess_opt = SessionOptions()
        sess_opt.log_severity_level = 4
        sess_opt.enable_cpu_mem_arena = False
        sess_opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        # Keep CPU threads low — thermal hard rule
        sess_opt.intra_op_num_threads = int(os.environ.get("HIGHLIGHT_ORT_THREADS") or 2)
        sess_opt.inter_op_num_threads = 1

        ep_list: list = []
        if "DmlExecutionProvider" in providers:
            ep_list.append("DmlExecutionProvider")
        elif config.get("use_cuda") and "CUDAExecutionProvider" in providers:
            ep_list.append(
                (
                    "CUDAExecutionProvider",
                    {
                        "device_id": 0,
                        "arena_extend_strategy": "kNextPowerOfTwo",
                        "cudnn_conv_algo_search": "HEURISTIC",
                        "do_copy_in_default_stream": True,
                    },
                )
            )
        ep_list.append(
            ("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})
        )

        ort_utils.OrtInferSession._verify_model(config["model_path"])
        self.session = InferenceSession(
            config["model_path"],
            sess_options=sess_opt,
            providers=ep_list,
        )

    ort_utils.OrtInferSession.__init__ = _init  # type: ignore[method-assign]
    ort_utils.OrtInferSession._highlight_patched = True  # type: ignore[attr-defined]
    return chosen


def _model_kwargs() -> dict:
    models = _resources() / "models" / "rapidocr"
    kwargs: dict = {}
    det = models / "ch_PP-OCRv4_det_infer.onnx"
    rec = models / "ch_PP-OCRv4_rec_infer.onnx"
    cls = models / "ch_ppocr_mobile_v2.0_cls_infer.onnx"
    if det.exists() and rec.exists():
        kwargs["det_model_path"] = str(det)
        kwargs["rec_model_path"] = str(rec)
        if cls.exists():
            kwargs["cls_model_path"] = str(cls)
    else:
        # Package defaults — still pass paths so det_use_cuda updater KeyError is avoided
        try:
            import rapidocr_onnxruntime
            from rapidocr_onnxruntime.utils import concat_model_path, read_yaml

            root = Path(rapidocr_onnxruntime.__file__).resolve().parent
            cfg = concat_model_path(read_yaml(str(root / "config.yaml")))
            kwargs["det_model_path"] = cfg["Det"]["model_path"]
            kwargs["rec_model_path"] = cfg["Rec"]["model_path"]
            kwargs["cls_model_path"] = cfg["Cls"]["model_path"]
        except Exception:
            pass
    return kwargs


def _try_rapidocr():
    global _ENGINE, _EP_NAME
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        try:
            from rapidocr import RapidOCR  # type: ignore
        except ImportError:
            return None

    _EP_NAME = _patch_ort_gpu_providers()
    kwargs = _model_kwargs()
    # Angle cls off — upright HUD. use_cuda flags enable DML/CUDA path in our patch.
    kwargs.update(
        {
            "use_angle_cls": False,
            "det_use_cuda": True,
            "rec_use_cuda": True,
            "cls_use_cuda": True,
        }
    )
    try:
        _ENGINE = RapidOCR(**kwargs)
        return f"rapidocr-{_EP_NAME}"
    except Exception as exc:
        print(f"rapidocr init fail: {exc}", file=sys.stderr, flush=True)
        try:
            _ENGINE = RapidOCR(**{k: v for k, v in kwargs.items() if not k.endswith("use_cuda")})
            _EP_NAME = "cpu"
            return "rapidocr-cpu"
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


def get_ocr_ep() -> str:
    """Active ONNX execution provider label: dml | cuda | cpu."""
    get_backend_name()
    return _EP_NAME


def _preprocess(path: Path, *, region: str) -> Path:
    """Contrast + light upscale for small feed crops. Tip skips heavy resize."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError:
        return path
    try:
        im = Image.open(path).convert("L")
        im = ImageOps.autocontrast(im)
        im = ImageEnhance.Contrast(im).enhance(1.8)
        if region != "tip":
            if im.width < 480:
                im = im.resize((im.width * 2, im.height * 2), Image.Resampling.LANCZOS)
            elif im.width < 700:
                im = im.resize(
                    (int(im.width * 1.5), int(im.height * 1.5)), Image.Resampling.LANCZOS
                )
        out = path.with_name(path.stem + "_ocrprep.jpg")
        im.convert("RGB").save(out, quality=90)
        return out
    except Exception:
        return path


def ocr_crop(path: Path | str, *, region: str = "feed") -> str:
    """
    OCR a single already-cropped image.
    region='tip' → lighter preprocess (no upscale). Always run det+rec —
    skipping det on large tip crops was slower/worse under DirectML.
    """
    p = Path(path)
    if not p.exists():
        return ""
    prep = _preprocess(p, region=region)
    backend = get_backend_name()
    text = ""
    if backend.startswith("rapidocr") and _ENGINE is not None:
        try:
            _ENGINE.use_text_det = True
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
