"""AI title / description / thumbnail base via OpenAI, Gemini, or OpenAI-compatible APIs (stdlib HTTP)."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

TIMEOUT = 180


class AiError(RuntimeError):
    pass


def _key(provider: dict) -> str:
    key = os.environ.get("HIGHLIGHT_AI_KEY") or provider.get("api_key") or ""
    if not key:
        raise AiError("missing API key")
    return key


def _base(provider: dict) -> str:
    return str(provider.get("base_url") or "").rstrip("/")


def _request(url: str, data: bytes, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        raise AiError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise AiError(f"network: {e.reason}") from e


def _post_json(url: str, body: dict, headers: dict[str, str]) -> dict:
    return _request(
        url,
        json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json", **headers},
    )


def _multipart(fields: dict[str, str], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    for name, path in files:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        out += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _parse_json_text(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AiError("model returned no JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AiError(f"bad JSON from model: {e}") from e


def chat_json(provider: dict, system: str, user: str) -> dict:
    kind = provider.get("type")
    model = provider.get("text_model") or ""
    if not model:
        raise AiError("no text model configured")
    key = _key(provider)
    if kind == "gemini":
        resp = _post_json(
            f"{_base(provider)}/models/{model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"},
            },
            {"x-goog-api-key": key},
        )
        parts = ((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
    else:
        body: dict[str, Any] = {
            "model": model,
            "temperature": 0.9,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if kind == "openai":
            body["response_format"] = {"type": "json_object"}
        resp = _post_json(
            f"{_base(provider)}/chat/completions",
            body,
            {"Authorization": f"Bearer {key}"},
        )
        text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return _parse_json_text(text)


def _openai_size(aspect: str) -> str:
    return "1024x1536" if aspect == "9:16" else "1536x1024"


def _image_from_openai_resp(resp: dict) -> bytes:
    item = (resp.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=TIMEOUT) as r:
            return r.read()
    raise AiError("image response had no data")


def generate_image(provider: dict, prompt: str, refs: list[Path], aspect: str) -> bytes:
    kind = provider.get("type")
    model = provider.get("image_model") or ""
    if not model:
        raise AiError("no image model configured")
    key = _key(provider)
    if kind == "gemini":
        parts: list[dict] = [
            {
                "inlineData": {
                    "mimeType": mimetypes.guess_type(p.name)[0] or "image/png",
                    "data": base64.b64encode(p.read_bytes()).decode("ascii"),
                }
            }
            for p in refs
        ]
        parts.append({"text": prompt})
        resp = _post_json(
            f"{_base(provider)}/models/{model}:generateContent",
            {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {"aspectRatio": aspect},
                },
            },
            {"x-goog-api-key": key},
        )
        for part in ((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []:
            data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
            if data:
                return base64.b64decode(data)
        raise AiError("Gemini returned no image")

    size = _openai_size(aspect)
    if kind == "openai" and refs:
        body, ctype = _multipart(
            {"model": model, "prompt": prompt, "size": size, "n": "1"},
            [("image[]", p) for p in refs],
        )
        resp = _request(
            f"{_base(provider)}/images/edits",
            body,
            {"Content-Type": ctype, "Authorization": f"Bearer {key}"},
        )
        return _image_from_openai_resp(resp)
    resp = _post_json(
        f"{_base(provider)}/images/generations",
        {"model": model, "prompt": prompt, "size": size, "n": 1},
        {"Authorization": f"Bearer {key}"},
    )
    return _image_from_openai_resp(resp)


_COUNT_TITLE = re.compile(r"^\s*\d+\s*(x\s*)?(kills?|frags?|plays?|knocks?|assists?)\b", re.I)

COPY_SYSTEM = """You write YouTube / Shorts packaging for PUBG gameplay highlights.
Return ONLY a JSON object: {"title": string, "description": string}.

Title rules (hard):
- Short punchy hook from the fight vibe (weapons, multi-kill, clutch, assist chain, squad fight), max ~45 chars before the suffix.
- End with the exact suffix given by the user.
- NEVER lead with or centre on a kill / frag / event count (no "5 Kills", "N FRAGS", "N PLAYS").
- No emojis, no quotes.

Description rules:
- First line = the title.
- 2-3 short lines on what happens (player POV, fights, weapons if known).
- A "Keywords:" line with comma separated search terms.
- A final hashtag line. Always #PUBG #PUBGHighlights. Add any extra tags requested.
- Never use #ChickenDinner unless the user says a win is confirmed.
"""


def generate_copy(provider: dict, ctx: dict) -> dict:
    user = json.dumps(ctx, indent=2)
    data = chat_json(provider, COPY_SYSTEM, user)
    title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip().strip('"')
    desc = str(data.get("description") or "").strip()
    suffix = str(ctx.get("title_suffix") or "| PUBG")
    if title and _COUNT_TITLE.search(title):
        title = ""
    if title and not title.endswith(suffix):
        title = f"{title.split('|')[0].strip()} {suffix}"
    return {"title": title, "description": desc}


def thumb_prompt(hook: str, aspect: str, has_character: bool, has_cover: bool) -> str:
    frame = "Vertical 9:16 YouTube Shorts" if aspect == "9:16" else "Landscape 16:9 YouTube"
    refs = []
    if has_character:
        refs.append("the FIRST reference image is the hero character - match face, outfit and pose vibe")
    if has_cover:
        refs.append("the cover reference sets the colour grade and mood")
    ref_line = ("; ".join(refs) + ". ") if refs else ""
    return (
        f"{frame} thumbnail art for a PUBG battle royale highlight titled '{hook}'. "
        f"{ref_line}"
        "The character is the dominant hero, mid-action, intense expression, cinematic dramatic lighting, "
        "muzzle flash, smoke, debris, high contrast, saturated, eye-catching. "
        "Keep the lower third relatively clean for text overlay. "
        "NO text, NO letters, NO logos, NO brand marks, NO watermarks, NO circular badges or avatars."
    )
