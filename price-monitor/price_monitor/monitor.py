"""One monitoring run: fetch every product, store the result, record changes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .alerts import Change, detect_changes
from .config import Config
from .extract import ExtractionError, extract
from .fetch import Fetcher, FetchError
from .store import Store, to_cents

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    observed_at: str
    ok: int = 0
    failed: int = 0
    changes: list[tuple[str, str, Change]] = field(default_factory=list)  # (url, title, change)
    errors: list[tuple[str, str]] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run(config: Config, store: Store, fetcher: Fetcher, now: str | None = None) -> RunSummary:
    now = now or utc_now()
    summary = RunSummary(observed_at=now)

    for product in config.products:
        product_id = store.upsert_product(product.url, product.name, product.target_price, now)
        try:
            data = extract(fetcher.get(product.url), product.url)
        except (FetchError, ExtractionError) as exc:
            # One broken page should not stop the rest of the run.
            log.warning("FAILED %s: %s", product.url, exc)
            store.add_observation(product_id, now, status="error", error=str(exc)[:500])
            summary.failed += 1
            summary.errors.append((product.url, str(exc)))
            store.commit()
            continue

        price_cents = to_cents(data.price)
        previous = store.last_ok(product_id, before=now)
        changes = detect_changes(
            previous,
            price_cents,
            data.in_stock,
            to_cents(product.target_price),
            config.settings.drop_threshold_pct,
        )
        store.add_observation(
            product_id,
            now,
            status="ok",
            price_cents=price_cents,
            currency=data.currency,
            in_stock=None if data.in_stock is None else int(data.in_stock),
            title=data.title,
            method=data.method,
        )
        title = product.name or data.title or product.url
        for change in changes:
            store.add_event(product_id, now, change.kind, change.old_cents, change.new_cents, change.pct_change)
            summary.changes.append((product.url, title, change))
        store.commit()
        summary.ok += 1
        log.info("ok %s %s %s", title, data.price, data.currency or "")

    return summary
