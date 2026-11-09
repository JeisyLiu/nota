"""Deterministic Chinese time-expression parser.

Two reasons this exists:

* the scripted (offline) planner needs it, and
* even with a real LLM, "下周三" is ambiguous unless you pin it against a
  concrete `now`. The harness injects the parsed absolute time into the tool
  arguments and validates whatever the model returns.

Supported: 今天/明天/后天/大后天, 周X / 星期X / 礼拜X (with optional 下周/这周),
上午/早上/中午/下午/晚上 + N点/M分, and "N点" alone (means today or tomorrow).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_DEFAULT_HOUR = {"上午": 9, "早上": 8, "中午": 12, "下午": 14, "晚上": 19, "傍晚": 18}
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
            "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
             "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_number(token: str) -> int | None:
    """Parse 一 … 十二 / 二十三 / 半 (handled by caller)."""
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_DIGIT.get(head, 1) if head else 1
        ones = _CN_DIGIT.get(tail, 0) if tail else 0
        return tens * 10 + ones
    total = 0
    for ch in token:
        if ch not in _CN_DIGIT:
            return None
        total = total * 10 + _CN_DIGIT[ch]
    return total


def _tz(tz_name: str):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name or "Asia/Shanghai")
        except Exception:
            pass
    return timezone(timedelta(hours=8))


def parse_when(text: str, now: datetime, tz_name: str = "Asia/Shanghai") -> datetime | None:
    """Return an absolute time if the text contains one, else ``None``."""
    if not text:
        return None
    tz = _tz(tz_name)
    local = now.astimezone(tz)
    day_offset: int | None = None
    explicit_weekday: int | None = None
    week_shift = 0

    if "大后天" in text:
        day_offset = 3
    elif "后天" in text:
        day_offset = 2
    elif "明天" in text or "明日" in text:
        day_offset = 1
    elif "今天" in text or "今日" in text:
        day_offset = 0

    m = re.search(r"(下下|下|这|本)?(?:周|星期|礼拜)([一二三四五六日天1-7])", text)
    if m:
        prefix, wd = m.group(1), m.group(2)
        explicit_weekday = _WEEKDAY.get(wd)
        if prefix == "下":
            week_shift = 1
        elif prefix == "下下":
            week_shift = 2
        elif prefix in ("这", "本"):
            week_shift = 0

    hour: int | None = None
    minute = 0
    period = None
    for key in _DEFAULT_HOUR:
        if key in text:
            period = key
            break

    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(
            r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*[点时]\s*"
            r"(半|\d{1,2}\s*分?|[零一二两三四五六七八九十]{1,3}\s*分?)?",
            text,
        )
        if m:
            hour = _cn_number(m.group(1))
            tail = (m.group(2) or "").strip()
            if tail == "半":
                minute = 30
            elif tail:
                parsed = _cn_number(tail.rstrip("分").strip())
                minute = parsed if parsed is not None else 0

    if hour is not None and period in ("下午", "晚上", "傍晚") and hour < 12:
        hour += 12
    if hour is None:
        hour = _DEFAULT_HOUR.get(period or "", None)

    if day_offset is None and explicit_weekday is None and hour is None:
        return None

    base = local
    if explicit_weekday is not None:
        days_ahead = (explicit_weekday - local.weekday()) % 7
        if week_shift:
            days_ahead += 7 * week_shift
        base = local + timedelta(days=days_ahead)
    elif day_offset is not None:
        base = local + timedelta(days=day_offset)

    if hour is None:
        hour = 9
    candidate = base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        hours=hour, minutes=minute
    )
    # A bare hour that has already passed means "tomorrow at that hour"; an hour
    # that is exactly now is still valid today.
    if day_offset is None and explicit_weekday is None and candidate < local:
        candidate += timedelta(days=1)
    return candidate


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds")


def detect_when(text: str, now: datetime, tz_name: str = "Asia/Shanghai") -> str | None:
    return to_iso(parse_when(text, now, tz_name))
