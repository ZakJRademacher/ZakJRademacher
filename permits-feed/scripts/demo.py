"""Generate sample_output/ by running the real feed against a simulated portal.

The portal serves synthetic permits shaped like Chicago's dataset. Some are
posted a week or two late and some have their cost corrected later, so the
demo shows the feed catching both. It runs the normal pipeline once a week,
September 7 to November 16, then writes the reports from the final run.

    python scripts/demo.py
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_socrata import FakeSocrata  # noqa: E402
from synthetic import COLUMNS, make_permits, visible_on  # noqa: E402

from permits_feed.config import load_config  # noqa: E402
from permits_feed.feed import run  # noqa: E402
from permits_feed.report import export  # noqa: E402
from permits_feed.store import Store  # noqa: E402

FIRST_RUN = date(2026, 9, 7)
LAST_RUN = date(2026, 11, 16)


def main() -> None:
    portal = FakeSocrata("demo-permits", COLUMNS)
    base = load_config(ROOT / "permits.toml")
    config = dataclasses.replace(
        base,
        source=dataclasses.replace(
            base.source, name="Demo city building permits (synthetic)", domain=portal.domain, dataset="demo-permits"
        ),
        settings=dataclasses.replace(base.settings, scheme="http", delay_seconds=0),
    )
    rows = make_permits(date(2026, 8, 20), LAST_RUN)

    out = ROOT / "sample_output"
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "permits.db"
        day = FIRST_RUN
        with Store(db) as store:
            print("run date    window from  fetched  new  updated  unreadable")
            while day <= LAST_RUN:
                portal.rows = visible_on(rows, day)
                s = run(config, store, now=f"{day.isoformat()}T13:17:00+00:00")
                print(f"{day}  {s.window_start}  {s.fetched:>7}  {s.new:>3}  {s.updated:>7}  {sum(s.issues.values()):>10}")
                day += timedelta(days=7)
        portal.close()

        if out.exists():
            shutil.rmtree(out)
        out.mkdir()
        shutil.copy(db, out / "permits.db")
    for path in export(out / "permits.db", out, config):
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
