"""Load the watch list and settings from a TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Product:
    url: str
    name: str | None = None
    target_price: Decimal | None = None  # alert when the price is at or below this


@dataclass(frozen=True)
class Settings:
    user_agent: str = "price-monitor/0.1 (+https://github.com/ZakJRademacher)"
    delay_seconds: float = 3.0  # minimum gap between requests to the same host
    timeout_seconds: float = 20.0
    max_retries: int = 3
    drop_threshold_pct: float = 1.0  # ignore drops smaller than this
    respect_robots: bool = True
    cache_hours: float = 12.0  # reuse a fetched page instead of hitting the site again


@dataclass(frozen=True)
class Config:
    settings: Settings
    products: list[Product] = field(default_factory=list)


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    known = set(Settings.__dataclass_fields__)
    settings_raw = raw.get("settings", {})
    unknown = set(settings_raw) - known
    if unknown:
        raise ConfigError(f"Unknown settings: {', '.join(sorted(unknown))}")
    settings = Settings(**settings_raw)

    products: list[Product] = []
    seen: set[str] = set()
    for i, item in enumerate(raw.get("product", []), start=1):
        url = str(item.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConfigError(f"product #{i}: url must be an http(s) URL, got {url!r}")
        if url in seen:
            raise ConfigError(f"product #{i}: duplicate url {url}")
        seen.add(url)
        target = item.get("target_price")
        products.append(
            Product(
                url=url,
                name=item.get("name"),
                target_price=Decimal(str(target)) if target is not None else None,
            )
        )
    if not products:
        raise ConfigError("No [[product]] entries found")
    return Config(settings=settings, products=products)
