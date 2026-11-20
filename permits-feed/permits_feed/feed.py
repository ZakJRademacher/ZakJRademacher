"""One feed run: fetch recent records, clean them, and save what's new or changed."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .clean import CleanError, clean_value, soql_date
from .config import Config
from .socrata import SocrataClient, SocrataError
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    run_id: int
    window_start: date
    fetched: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0  # rows with no id
    issues: Counter = field(default_factory=Counter)  # column -> unreadable values


def window_start(config: Config, store: Store) -> date:
    """First run: start_date. Later runs: re-check `lookback_days` before the newest record."""
    latest = store.latest_record_date()
    if latest is None:
        return config.source.start_date
    return max(config.source.start_date, date.fromisoformat(latest) - timedelta(days=config.source.lookback_days))


def build_query(config: Config, start: date) -> tuple[list[str], str, list[str]]:
    src = config.source
    select = list(dict.fromkeys([src.id_field, *config.fields.values()]))
    where = f"{src.date_field} >= {soql_date(start)}"
    if src.where:
        where += f" AND ({src.where})"
    order = [src.date_field, src.id_field]
    return select, where, order


def clean_row(config: Config, row: dict, issues: Counter) -> dict:
    out = {}
    for column, source_field in config.fields.items():
        try:
            out[column] = clean_value(row.get(source_field), config.types.get(column, "text"))
        except CleanError as exc:
            issues[column] += 1
            log.debug("record %s, %s: %s", row.get(config.source.id_field), column, exc)
            out[column] = None
    return out


def run(config: Config, store: Store, client: SocrataClient | None = None, now: str | None = None) -> RunSummary:
    own_client = client is None
    client = client or SocrataClient(config.source.domain, config.settings)
    now = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    start = window_start(config, store)
    run_id = store.start_run(now, start.isoformat())
    summary = RunSummary(run_id=run_id, window_start=start)
    date_col = config.date_column

    try:
        select, where, order = build_query(config, start)
        # Fail fast with a clear message if the portal renamed or dropped a field.
        client.check_fields(config.source.dataset, select)
        for row in client.rows(config.source.dataset, select, where, order):
            summary.fetched += 1
            record_id = row.get(config.source.id_field)
            if record_id in (None, ""):
                summary.skipped += 1
                continue
            data = clean_row(config, row, summary.issues)
            result = store.upsert(run_id, str(record_id), data.get(date_col), data)
            if result == "new":
                summary.new += 1
            elif result == "updated":
                summary.updated += 1
    except SocrataError as exc:
        # Don't keep half a run: the next run will fetch the same window again.
        store.rollback()
        store.finish_run(run_id, status="failed", fetched=summary.fetched, error=str(exc)[:1000])
        raise
    finally:
        if own_client:
            client.close()

    store.finish_run(
        run_id,
        status="ok",
        fetched=summary.fetched,
        new=summary.new,
        updated=summary.updated,
        skipped=summary.skipped,
        issues=dict(summary.issues),
    )
    return summary
