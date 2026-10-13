"""Kill-feed + tip classification — me/clan on left = my frag; right-only = skip."""

from __future__ import annotations

import re

SPEC_PAT = re.compile(r"spectat|death\s*cam|watching|observe", re.I)
VICTIM_ME = re.compile(
    r"(knocked|killed|eliminated|cked\s*out).{0,40}\byou\b|"
    r"\bx\b.{0,20}\byou\b",
    re.I,
)
# Map / HUD chrome — not a frag
UI_CHROME = re.compile(
    r"^\s*(kills?\s*assists?|assists?\s*kills?|\d+\s*killed|\d+\s*assisted|\d+\s*alive)\s*$",
    re.I,
)
ASSIST_RE = re.compile(
    r"\bi\s*assist|\byou\s*assist|\+\s*\d+\s*assist|assist\s*\+|assisted\s+by",
    re.I,
)
MAP_ONLY_ASSIST = re.compile(r"^\s*\d+\s*assisted\s*$", re.I)

# Require YOU-like prefix — bare "KNOCKED OUT" is often a teammate tip
YOU_LIKE = re.compile(
    r"\byou\b|\by[o0]u\b|\byoo\b|\byouik|\byoui\s*k|\bu\s*kil",
    re.I,
)
YOU_KNOCK = re.compile(
    r"\byou\s*kn[o0]ck|\byou\s*d[o0]wn|\by[o0]u\s*kn[o0]ck|"
    r"\byou\s*kn[o0]c|\byou\s*knocke[od]?|"
    r"\byou\s*kn[\s\[]|\byou\s*kn\b|\byoo\s*k\b|"
    r"\byou\s*kn[o0]cke?\s*t|"
    r"\byou.{0,10}n[o0]cked?\s*o",
    re.I,
)
YOU_KILL = re.compile(
    r"\byou\s*kill|\byou\s*elimin|\by[o0]u\s*kill|"
    r"\byou\s*kille[ed]?|\byou\s*killl?|"
    r"\byou\s*ki[l1]{1,3}|\bu\s*kil[l1]|"
    # OCR garble: KIELED / KILED / KLLED / YOUCLLED / YOU KLI ED / YOU-KILLED
    r"\byou[\-\s]*k[il1e]{2,8}d|\byouc?l+e[d]|\byou\s*kli\s*e[d]|"
    r"\byouikll|\byoui\s*kll?|"
    r"\byou\s*with\s+\w|"
    r"\bwith\s+you[\-\s]*k[il1e]{2,8}d|"
    r"\byou.{0,28}\bkill(?:ed|vd|e[d3])?\b.{0,20}\bwith\b|"
    r"\byou.{0,28}\bhead\s*shot\b.{0,16}\bwith\b",
    re.I,
)
# Squad tip: "2 [ASA] TeammateName KNOCKED OUT victim" — not mine
TEAMMATE_TIP = re.compile(
    r"(?:^|\s)\d{0,2}\s*\[?[a-z0-9]{2,5}\]?[\s\)\]]+[a-z][a-z0-9_\-]{2,}"
    r".{0,36}(?:knocked\s*out|killed)\b",
    re.I,
)
TIP_VICTIM = re.compile(
    r"knocked\s+you|nocked\s+you|killed\s+you|cked\s+you\s+out|"
    r"you\s+were\s+(knocked|killed)|killed\s+by\b",
    re.I,
)
KILL_COUNTER = re.compile(
    r"\b([1-9]|1[0-9]|2[0-9])\s*kil+e[d]?\b|"
    r"\b([1-9]|1[0-9]|2[0-9])\s*kil+s?\b",
    re.I,
)
ASSIST_COUNTER = re.compile(r"\b([1-9]|1[0-9]|2[0-9])\s*assist", re.I)
TIP_KILL_COUNT = re.compile(
    r"\b([1-9]|1[0-9]|2[0-9])\s*kil+s?\b|"
    r"\bkil+s?\s*([1-9]|1[0-9]|2[0-9])\b",
    re.I,
)


def norm_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def name_pos(name: str, text: str) -> int | None:
    if not name or not text:
        return None
    t = text.lower()
    n = name.lower()
    idx = t.find(n)
    if idx >= 0:
        return idx
    stem = norm_token(name)
    if len(stem) < 4:
        return None
    for L in (8, 6, 5, 4):
        if len(stem) < L:
            continue
        s = stem[:L]
        compact = norm_token(text)
        cidx = compact.find(s)
        if cidx < 0:
            continue
        raw_i = 0
        comp_i = 0
        while raw_i < len(t) and comp_i < cidx:
            if t[raw_i].isalnum():
                comp_i += 1
            raw_i += 1
        return raw_i
    return None


def me_feed_pos(text: str, me: str, stems: list[str] | None = None) -> int | None:
    pos = name_pos(me, text)
    if pos is not None:
        return pos
    t = (text or "").lower()
    for stem in stems or []:
        if len(stem) < 4:
            continue
        i = t.find(stem.lower())
        if i >= 0:
            return i
    # OCR often drops leading r: ASAanik / [ASA]anik
    compact = norm_token(text)
    me_n = norm_token(me)
    if len(me_n) >= 5:
        tail = me_n[1:]  # kanik
        for prefix in ("asa", "asai", "tasa", ""):
            needle = prefix + tail
            if len(needle) < 5:
                continue
            idx = compact.find(needle)
            if idx >= 0:
                raw_i = comp_i = 0
                while raw_i < len(t) and comp_i < idx:
                    if t[raw_i].isalnum():
                        comp_i += 1
                    raw_i += 1
                return raw_i
    return None


def _glued_me_pos(text: str, me: str, stems: list[str] | None, clan_tags: list[str] | None) -> int | None:
    compact = norm_token(text)
    me_n = norm_token(me)
    clan_bits = [norm_token(c) for c in (clan_tags or ["asa"])] + ["asa", "asai", "tasa", "iasa"]
    candidates = [me_n] + [norm_token(s) for s in (stems or []) if len(norm_token(s)) >= 4]
    # r dropped / swapped
    if len(me_n) >= 5:
        candidates += [me_n[1:], "t" + me_n[1:], "l" + me_n[1:], "i" + me_n[1:]]
    for stem in candidates:
        if len(stem) < 4:
            continue
        for clan in clan_bits:
            needle = clan + stem
            idx = compact.find(needle)
            if idx >= 0:
                raw_i = comp_i = 0
                tl = text.lower()
                while raw_i < len(tl) and comp_i < idx:
                    if tl[raw_i].isalnum():
                        comp_i += 1
                    raw_i += 1
                return raw_i
        idx = compact.find(stem)
        if idx >= 0 and len(stem) >= 5:
            raw_i = comp_i = 0
            tl = text.lower()
            while raw_i < len(tl) and comp_i < idx:
                if tl[raw_i].isalnum():
                    comp_i += 1
                raw_i += 1
            return raw_i
    return None


def roster_victim(text: str, name: str) -> bool:
    pos = name_pos(name, text)
    if pos is None:
        return False
    before = text.lower()[max(0, pos - 48): pos]
    return bool(re.search(r"(knocked|killed|eliminated|cked\s*out)\b", before))


def classify_assist(text: str) -> str | None:
    if not text or MAP_ONLY_ASSIST.search(text.strip()) or UI_CHROME.search(text.strip()):
        return None
    # Bare "KILLS ASSISTS" HUD
    if re.search(r"\bkills?\s+assists?\b", text, re.I) and not re.search(r"\bx\b|knock|you\s", text, re.I):
        return None
    if ASSIST_RE.search(text):
        if VICTIM_ME.search(text):
            return None
        return "my_assist"
    return None


def classify_tip_line(text: str) -> str | None:
    """Center tip: YOU KNOCKED OUT / YOU KILLED (OCR-tolerant)."""
    if not text or SPEC_PAT.search(text) or VICTIM_ME.search(text) or TIP_VICTIM.search(text):
        return None
    # Teammate/squad kill tip without YOU — never claim as mine
    if not YOU_LIKE.search(text) and TEAMMATE_TIP.search(text):
        return None
    if YOU_KILL.search(text):
        return "my_kill"
    if YOU_KNOCK.search(text):
        return "my_knock"
    # Garbled tip: weapon + knock/kill only when YOU-like prefix present
    if YOU_LIKE.search(text) and re.search(
        r"\bwith\s+(aug|akm|m416|m24|vector|mp5|frag|kar98|beryl|m762|mp5k)\b",
        text,
        re.I,
    ):
        if re.search(r"knock|kn0ck|nock", text, re.I):
            return "my_knock"
        if re.search(r"kill|elimin|head\s*shot", text, re.I):
            return "my_kill"
    return None


def parse_kill_counter(text: str) -> int | None:
    m = KILL_COUNTER.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1) or m.group(2))
    except (TypeError, ValueError):
        return None


def tip_kill_count_events(ocr_map: dict[int, str]) -> list[dict]:
    """Emit my_kill when center 'N KILLS' overlay increments (tip crop)."""
    events: list[dict] = []
    last = None
    for t in sorted(ocr_map):
        text = ocr_map[t] or ""
        m = TIP_KILL_COUNT.search(text)
        if not m:
            continue
        try:
            n = int(m.group(1) or m.group(2))
        except (TypeError, ValueError):
            continue
        if last is not None and n > last and n - last <= 3:
            events.append({
                "t": float(t),
                "kind": "my_kill",
                "text": f"tip_kill_count {last}->{n} | {text[:80]}",
                "source": "tip_kill_count",
            })
        last = n
    return events


def parse_assist_counter(text: str) -> int | None:
    m = ASSIST_COUNTER.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def classify_feed_line(
    text: str,
    me: str,
    team: list[str],
    stems: list[str] | None = None,
    clan_tags: list[str] | None = None,
) -> str | None:
    """
    Kill feed: killer left → victim right.
    Me left of X (finish icon) → my_kill; crawl/knock row without X → my_knock.
    X then me → skip (I got knocked).
    """
    if not text or SPEC_PAT.search(text):
        return None
    if UI_CHROME.search(text.strip()):
        return None
    t = text.lower()
    if VICTIM_ME.search(text):
        return None

    assist = classify_assist(text)
    me_pos = me_feed_pos(text, me, stems)
    if me_pos is None:
        me_pos = _glued_me_pos(text, me, stems, clan_tags)

    # Finish icon is cyan X; OCR often glues to victim ("Xviewhero"). Knock = crawl icon.
    x_all = [m.start() for m in re.finditer(r"(?<![a-z0-9])x|[×]", t)]

    if me_pos is not None and x_all:
        nearest_left = max((x for x in x_all if x < me_pos), default=None)
        nearest_right = min((x for x in x_all if x > me_pos + 1), default=None)
        # Multi-line OCR dumps: pick nearest X. Closer X on the left ⇒ I am the victim.
        if nearest_left is not None and (
            nearest_right is None or (me_pos - nearest_left) <= (nearest_right - me_pos)
        ):
            return None
        if nearest_right is not None:
            return "my_kill"

    clan_pat = "|".join(re.escape(c.lower()) for c in (clan_tags or ["asa"]))
    if me and re.search(
        rf"({clan_pat}|asa[\]i1l]?).{{0,10}}{re.escape(me.lower())}.{{0,24}}"
        rf"(?<![a-z0-9])x",
        t,
    ):
        return "my_kill"

    if me_pos is not None and me_pos <= max(14, int(len(t) * 0.60)):
        after = t[me_pos + max(3, len(norm_token(me)) - 2) :]
        if re.search(r"kill|elimin", after[:80]):
            return "my_kill"
        if re.search(r"knock", after[:80]):
            return "my_knock"
        # me then victim name, no X → knock row (crawl icon). Require name-ish token.
        if re.search(r"[a-z]{4,}", after) and not re.search(
            r"^\s*\d+\s*(mm|alive)?\s*$", after.strip()
        ):
            if "alive" in after and len(after) < 20:
                return assist
            # Skip pure HUD mash (ALIVE / ammo) when no second player token after me
            if re.search(r"\b(alive|aliye|aliv[ec]|7\.62|5\.45|mm)\b", after) and not re.search(
                r"[a-z]{4,}", re.sub(r"\b(alive|aliye|aliv[ec]|standard|mm)\b", " ", after)
            ):
                return assist
            return "my_knock"

    hits: list[tuple[int, str, bool]] = []
    for n in team:
        pos = name_pos(n, text)
        if pos is None:
            continue
        is_me = norm_token(me) in norm_token(n) or norm_token(n) in norm_token(me)
        hits.append((pos, n, is_me))
    if not hits:
        return assist
    hits.sort(key=lambda x: x[0])
    pos, name, is_me = hits[0]

    if pos > max(12, int(len(t) * 0.58)):
        return None
    if roster_victim(text, name):
        return None
    if re.search(rf"(?<![a-z0-9])x\s*\[?\s*asa\]?\s*{re.escape(name[:5])}", t, re.I):
        return None

    rest = t[pos: pos + 80]
    if is_me:
        if re.search(r"(?<![a-z0-9])x|[×]", rest):
            return "my_kill"
        if re.search(r"knock", rest):
            return "my_knock"
        if re.search(r"kill|elimin|with\s+\w|head\s*shot|dshot", rest):
            return "my_kill"
        if re.search(r"[a-z]{4,}", rest[len(name) :]):
            return "my_knock"
        return None

    return "my_assist"


def counter_events_from_ocr(
    ocr_map: dict[int, str],
    *,
    kind_kill: str = "my_kill",
    kind_assist: str = "my_assist",
) -> list[dict]:
    """Emit events when top-right KILLED / ASSISTED counters increment."""
    events: list[dict] = []
    last_k = None
    last_a = None
    for t in sorted(ocr_map):
        text = ocr_map[t] or ""
        k = parse_kill_counter(text)
        a = parse_assist_counter(text)
        if k is not None:
            if last_k is not None and k > last_k and k - last_k <= 3:
                events.append({
                    "t": float(t),
                    "kind": kind_kill,
                    "text": f"kill_counter {last_k}->{k} | {text[:80]}",
                    "source": "kill_counter",
                })
            last_k = k
        if a is not None:
            if last_a is not None and a > last_a and a - last_a <= 3:
                events.append({
                    "t": float(t),
                    "kind": kind_assist,
                    "text": f"assist_counter {last_a}->{a} | {text[:80]}",
                    "source": "assist_counter",
                })
            last_a = a
    return events


def dedupe_events(events: list[dict], gap: float = 1.8) -> list[dict]:
    events = sorted(events, key=lambda e: e["t"])
    dedup: list[dict] = []
    rank = {"my_kill": 4, "my_knock": 3, "my_assist": 2, "my_kill_or_knock": 1}
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < gap:
            if rank.get(e["kind"], 0) > rank.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)
    return dedup


def filter_include(events: list[dict], include: set[str]) -> list[dict]:
    if not include:
        return events
    return [e for e in events if e.get("kind") in include]
