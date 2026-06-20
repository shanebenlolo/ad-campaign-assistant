from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DATE_RANGE_RE = re.compile(r"^[A-Z0-9_]+$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_customer_id(customer_id: str) -> str:
    normalized = customer_id.replace("-", "").strip()
    if not normalized.isdigit():
        raise ValueError("Google Ads customer ID must contain only digits or dashes")
    return normalized


def gaql_date_filter(date_range: str) -> str:
    value = date_range.strip()
    if "," in value:
        start, end = [part.strip() for part in value.split(",", 1)]
        if not ISO_DATE_RE.match(start) or not ISO_DATE_RE.match(end):
            raise ValueError(
                "Custom date ranges must be formatted as YYYY-MM-DD,YYYY-MM-DD"
            )
        return f"segments.date BETWEEN '{start}' AND '{end}'"
    if not DATE_RANGE_RE.match(value):
        raise ValueError(
            "Date range must be a Google Ads range constant, or YYYY-MM-DD,YYYY-MM-DD"
        )
    return f"segments.date DURING {value}"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, record: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def micros_to_currency(micros: int | float | None) -> float:
    if micros is None:
        return 0.0
    return round(float(micros) / 1_000_000, 2)


def compact_domain(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc or parsed.path
    host = host.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def enum_name(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]

