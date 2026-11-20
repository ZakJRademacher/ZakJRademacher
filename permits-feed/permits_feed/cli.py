"""Command line entry point: python -m permits_feed <command>."""

from __future__ import annotations

import argparse
import logging

from .config import load_config
from .feed import run
from .report import export
from .socrata import SocrataClient, SocrataError
from .store import Store


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with Store(args.db) as store:
        try:
            s = run(config, store)
        except SocrataError as exc:
            print(f"FAILED: {exc}")
            return 1
    print(f"\n{config.source.name}: checked records since {s.window_start}")
    print(f"  fetched {s.fetched}, new {s.new}, updated {s.updated}, skipped (no id) {s.skipped}")
    for column, count in sorted(s.issues.items()):
        print(f"  {count} value(s) in {column} couldn't be read and were left blank")
    if args.report_dir:
        for path in export(args.db, args.report_dir, config):
            print(f"wrote {path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    for path in export(args.db, args.out, config, chart=not args.no_chart):
        print(f"wrote {path}")
    return 0


def cmd_fields(args: argparse.Namespace) -> int:
    """List a dataset's fields, to help write [fields] for a new portal."""
    config = load_config(args.config)
    domain = args.domain or config.source.domain
    dataset = args.dataset or config.source.dataset
    try:
        columns = SocrataClient(domain, config.settings).columns(dataset)
    except SocrataError as exc:
        print(f"FAILED: {exc}")
        return 1
    width = max(map(len, columns), default=0)
    for name, kind in columns.items():
        print(f"{name:<{width}}  {kind}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="permits_feed", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-c", "--config", default="permits.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="fetch new and changed records")
    p.add_argument("--db", default="data/permits.db")
    p.add_argument("--report-dir", help="also write the spreadsheet, CSVs and chart here")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="write the spreadsheet, CSVs and chart from the database")
    p.add_argument("--db", default="data/permits.db")
    p.add_argument("-o", "--out", default="reports")
    p.add_argument("--no-chart", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("fields", help="list a dataset's field names and types")
    p.add_argument("--domain", help="portal domain (default: from the config)")
    p.add_argument("--dataset", help="dataset id (default: from the config)")
    p.set_defaults(func=cmd_fields)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    return args.func(args)
