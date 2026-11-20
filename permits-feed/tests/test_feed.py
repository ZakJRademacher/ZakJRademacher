import json
import sqlite3
from datetime import date

import pytest
from synthetic import make_permits, visible_on

from permits_feed.feed import run
from permits_feed.report import export
from permits_feed.socrata import SchemaError, SocrataError
from permits_feed.store import Store

ROWS = make_permits(date(2026, 9, 1), date(2026, 11, 15))


def run_on(portal, config, store, today):
    portal.rows = visible_on(ROWS, today)
    return run(config, store, now=f"{today.isoformat()}T12:00:00+00:00")


def expected_ids(today, start=date(2026, 9, 1)):
    return {r["id"] for r in visible_on(ROWS, today)
            if r["issue_date"][:10] >= start.isoformat() and float(r["reported_cost"]) >= 50000}


def test_first_run_fetches_everything_with_paging(portal, config, tmp_path):
    with Store(tmp_path / "p.db") as store:
        s = run_on(portal, config, store, date(2026, 10, 1))
    assert s.new == len(expected_ids(date(2026, 10, 1))) > 50  # more than one page
    pages = [r for r in portal.requests if r["path"].startswith("/resource/")]
    assert len(pages) == s.fetched // 50 + 1
    assert all(p["$order"] == "issue_date, id" for p in pages)
    assert pages[0]["$where"] == "issue_date >= '2026-09-01T00:00:00' AND (reported_cost >= 50000)"


def test_weekly_runs_catch_late_and_revised_records_without_duplicates(portal, config, tmp_path):
    db = tmp_path / "p.db"
    days = [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28), date(2026, 10, 5)]
    with Store(db) as store:
        summaries = [run_on(portal, config, store, d) for d in days]

    conn = sqlite3.connect(db)
    stored = {r[0] for r in conn.execute("SELECT id FROM records")}
    assert stored == expected_ids(days[-1])  # nothing missed, nothing extra
    assert sum(s.new for s in summaries) == len(stored)  # each record counted as new exactly once

    # Late-posted records: issued before the previous run, first seen in a later run.
    late = conn.execute(
        """SELECT COUNT(*) FROM records r JOIN runs prev ON prev.run_id = r.first_seen_run - 1
           WHERE r.record_date < substr(prev.ran_at, 1, 10)"""
    ).fetchone()[0]
    assert late > 0

    # Revised costs are picked up as updates and the stored value is the corrected one.
    assert sum(s.updated for s in summaries) > 0
    for rid, data in conn.execute("SELECT id, data FROM records WHERE last_changed_run > first_seen_run"):
        source = next(r for r in visible_on(ROWS, days[-1]) if r["id"] == rid)
        assert json.loads(data)["reported_cost"] == float(source["reported_cost"])


def test_personal_fields_are_never_stored(portal, config, tmp_path):
    db = tmp_path / "p.db"
    with Store(db) as store:
        run_on(portal, config, store, date(2026, 9, 20))
    dump = "\n".join(sqlite3.connect(db).iterdump())
    names = {r["contact_1_name"] for r in ROWS}
    assert not any(name in dump for name in names)
    select = next(r for r in portal.requests if r["path"].startswith("/resource/"))["$select"]
    assert "contact_1_name" not in select


def test_bad_values_are_counted_and_blanked(portal, config, tmp_path):
    with Store(tmp_path / "p.db") as store:
        s = run_on(portal, config, store, date(2026, 11, 15))
    bad_wards = sum(1 for r in visible_on(ROWS, date(2026, 11, 15))
                    if r["ward"] == "4.5" and float(r["reported_cost"]) >= 50000)
    assert bad_wards > 0 and s.issues == {"ward": bad_wards}


def test_renamed_field_fails_fast_with_available_fields(portal, config, tmp_path):
    del portal.columns["reported_cost"]
    with Store(tmp_path / "p.db") as store, pytest.raises(SchemaError, match="no field.*reported_cost.*Available fields: .*permit_type"):
        run_on(portal, config, store, date(2026, 9, 20))


def test_failed_run_keeps_no_partial_data(portal, config, tmp_path):
    db = tmp_path / "p.db"
    portal.fail_with = 500
    with Store(db) as store, pytest.raises(SocrataError, match="HTTP 500"):
        run_on(portal, config, store, date(2026, 9, 20))
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM runs").fetchall() == [("failed",)]

    portal.fail_with = None  # the next run starts from the same window
    with Store(db) as store:
        s = run_on(portal, config, store, date(2026, 9, 20))
    assert s.window_start == date(2026, 9, 1) and s.new == len(expected_ids(date(2026, 9, 20)))


def test_report_outputs(portal, config, tmp_path):
    db = tmp_path / "p.db"
    with Store(db) as store:
        run_on(portal, config, store, date(2026, 10, 5))
        last = run_on(portal, config, store, date(2026, 10, 12))
    paths = export(db, tmp_path / "out", config)
    assert {p.name for p in paths} == {"new_records.csv", "all_records.csv", "test-permits_2026-10-12.xlsx", "weekly_trend.png"}

    import pandas as pd
    sheets = pd.read_excel(tmp_path / "out" / "test-permits_2026-10-12.xlsx", sheet_name=None)
    assert list(sheets) == ["new_this_run", "by_permit_type", "by_community_area", "all_records", "runs"]
    assert len(sheets["new_this_run"]) == last.new
    assert sheets["by_permit_type"]["records"].sum() == last.new
    assert sheets["new_this_run"]["address"].str.match(r"^\d+ [NSEW] ").all()
