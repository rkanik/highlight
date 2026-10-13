"""JSONL progress protocol for UI / CLI consumers."""

from __future__ import annotations

import json
import sys
from typing import Any


def emit(**fields: Any) -> None:
    # ensure_ascii=True: Windows cp1252/charmap consoles can't print ≥/— etc.
    line = json.dumps(fields, ensure_ascii=True)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.flush()


def stage(name: str, pct: float | None = None, message: str = "") -> None:
    payload: dict[str, Any] = {"stage": name, "message": message}
    if pct is not None:
        payload["pct"] = round(float(pct), 2)
    emit(**payload)


def artifact(path: str, kind: str = "file") -> None:
    emit(artifact=path, kind=kind)


def error(message: str) -> None:
    emit(error=message)


def done(ok: bool = True, **extra: Any) -> None:
    emit(done=True, ok=ok, **extra)
