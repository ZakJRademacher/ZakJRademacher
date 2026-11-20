"""Load the feed definition from a TOML file."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TYPES = ("text", "date", "money", "int", "float")


@dataclass(frozen=True)
class Source:
    name: str
    domain: str
    dataset: str
    id_field: str
    date_field: str
    start_date: date
    lookback_days: int = 14
    where: str | None = None


@dataclass(frozen=True)
class Settings:
    page_size: int = 1000
    delay_seconds: float = 1.0
    timeout_seconds: float = 30.0
    max_retries: int = 3
    user_agent: str = "permits-feed/0.1 (+https://github.com/ZakJRademacher)"
    scheme: str = "https"  # tests and the demo point this at a local http server


@dataclass(frozen=True)
class Report:
    group_by: list[str] = field(default_factory=list)
    value_field: str | None = None
    address: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Config:
    source: Source
    settings: Settings
    fields: dict[str, str]  # output column -> source field
    types: dict[str, str]  # output column -> type
    report: Report

    @property
    def date_column(self) -> str:
        """The output column holding the source's date field."""
        for out, src in self.fields.items():
            if src == self.source.date_field:
                return out
        raise ConfigError(f"[fields] must include the date field {self.source.date_field!r}")


class ConfigError(ValueError):
    pass


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _section(raw: dict, name: str, cls):
    data = raw.get(name, {})
    unknown = set(data) - set(cls.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"Unknown keys in [{name}]: {', '.join(sorted(unknown))}")
    return data


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    src = _section(raw, "source", Source)
    missing = [k for k in ("name", "domain", "dataset", "id_field", "date_field", "start_date") if k not in src]
    if missing:
        raise ConfigError(f"[source] is missing: {', '.join(missing)}")
    if isinstance(src["start_date"], str):
        src["start_date"] = date.fromisoformat(src["start_date"])
    source = Source(**src)

    settings = Settings(**_section(raw, "settings", Settings))
    report = Report(**_section(raw, "report", Report))

    fields = dict(raw.get("fields", {}))
    if not fields:
        raise ConfigError("[fields] is empty: list the columns to keep")
    for out, src_field in fields.items():
        if not _IDENT.match(out) or not _IDENT.match(str(src_field)):
            raise ConfigError(f"[fields] {out} = {src_field!r}: names must be letters, digits and underscores")
    if "id" in fields:
        raise ConfigError("[fields] can't define an output column named 'id' (it's reserved for the record id)")

    types = dict(raw.get("types", {}))
    for out, kind in types.items():
        if out not in fields:
            raise ConfigError(f"[types] {out} is not a column in [fields]")
        if kind not in TYPES:
            raise ConfigError(f"[types] {out} = {kind!r}: use one of {', '.join(TYPES)}")

    for col in [*report.group_by, *report.address, *([report.value_field] if report.value_field else [])]:
        if col not in fields:
            raise ConfigError(f"[report] refers to {col!r}, which is not a column in [fields]")

    config = Config(source=source, settings=settings, fields=fields, types=types, report=report)
    config.date_column  # noqa: B018 - validates that the date field is mapped
    return config
