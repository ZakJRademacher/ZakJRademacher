"""Command line entry point: python -m price_monitor <command>."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings, load_config
from .extract import ExtractionError, extract
from .fetch import Fetcher, FetchError
from .monitor import run
from .report import export
from .store import Store, from_cents

LABELS = {
    "price_drop": "PRICE DROP",
    "price_rise": "price up",
    "back_in_stock": "BACK IN STOCK",
    "out_of_stock": "out of stock",
    "target_hit": "TARGET HIT",
}


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    fetcher = Fetcher(config.settings, cache_dir=args.cache_dir)
    with Store(args.db) as store:
        summary = run(config, store, fetcher)

    print(f"\nChecked {summary.ok + summary.failed} products at {summary.observed_at}: "
          f"{summary.ok} ok, {summary.failed} failed")
    for url, title, ch in summary.changes:
        line = f"  {LABELS[ch.kind]:<14} {title}"
        if ch.old_cents is not None and ch.new_cents is not None:
            line += f": {from_cents(ch.old_cents)} -> {from_cents(ch.new_cents)}"
        if ch.pct_change is not None:
            line += f" ({ch.pct_change:+.1f}%)"
        print(line)
    for url, error in summary.errors:
        print(f"  FAILED         {url}: {error}")

    if args.report_dir:
        for path in export(args.db, args.report_dir):
            print(f"wrote {path}")
    # Non-zero exit when nothing worked, so a scheduler flags the run.
    return 1 if summary.ok == 0 else 0


def cmd_report(args: argparse.Namespace) -> int:
    for path in export(args.db, args.out, chart=not args.no_chart):
        print(f"wrote {path}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Extract one page and print what was found. Handy for sampling a client's site."""
    settings = Settings(delay_seconds=0, cache_hours=0)
    try:
        data = extract(Fetcher(settings).get(args.url), args.url)
    except (FetchError, ExtractionError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    stock = {True: "yes", False: "no", None: "unknown"}[data.in_stock]
    print(f"title:    {data.title}\nprice:    {data.price} {data.currency or ''}\n"
          f"in stock: {stock}\nfound by: {data.method}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="price_monitor", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="check every product in the config once")
    p_run.add_argument("-c", "--config", default="products.toml")
    p_run.add_argument("--db", default="data/prices.db")
    p_run.add_argument("--cache-dir", default=".cache")
    p_run.add_argument("--report-dir", help="also export CSV, Excel and a chart here")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="export history to CSV, Excel and a chart")
    p_report.add_argument("--db", default="data/prices.db")
    p_report.add_argument("-o", "--out", default="reports")
    p_report.add_argument("--no-chart", action="store_true")
    p_report.set_defaults(func=cmd_report)

    p_check = sub.add_parser("check", help="extract a single product page and print the result")
    p_check.add_argument("url")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return args.func(args)
