"""OCR backends — RapidOCR primary; Windows OCR fallback; never dense full-frame."""

from __future__ import annotations

from engine.ocr.backend import ocr_crop, get_backend_name

__all__ = ["ocr_crop", "get_backend_name"]
