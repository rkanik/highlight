"""OCR backends for sparse crop recognition."""

from engine.ocr.backend import get_backend_name, get_ocr_ep, ocr_crop

__all__ = ["ocr_crop", "get_backend_name", "get_ocr_ep"]
