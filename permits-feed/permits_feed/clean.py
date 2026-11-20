"""Turn raw Socrata values (mostly strings) into clean typed values."""

from __future__ import annotations

import re
from datetime import date, datetime


class CleanError(ValueError):
    pass


def _number(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[,$\s]", "", str(value))
    return float(text)


def clean_value(value, kind: str):
    """Return the cleaned value, None for blanks, or raise CleanError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if kind == "text":
            return " ".join(str(value).split())  # collapse stray whitespace and newlines
        if kind == "date":
            # SODA floating timestamps look like 2026-11-03T00:00:00.000
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
        if kind == "money":
            return round(_number(value), 2)
        if kind == "float":
            return _number(value)
        if kind == "int":
            number = _number(value)
            if number != int(number):
                raise CleanError(f"{value!r} is not a whole number")
            return int(number)
    except (ValueError, TypeError) as exc:
        raise CleanError(f"can't read {value!r} as {kind}") from exc
    raise CleanError(f"unknown type {kind!r}")


def soql_date(d: date) -> str:
    return f"'{d.isoformat()}T00:00:00'"
