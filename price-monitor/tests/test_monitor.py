import sqlite3

from price_monitor.config import Config, Product, Settings
from price_monitor.fetch import Fetcher
from price_monitor.monitor import run
from price_monitor.report import export
from price_monitor.store import Store


def jsonld_page(name, price, availability="InStock"):
    return f"""<html><head><script type="application/ld+json">
    {{"@type": "Product", "name": "{name}",
      "offers": {{"price": "{price}", "priceCurrency": "USD", "availability": "https://schema.org/{availability}"}}}}
    </script></head><body></body></html>"""


SETTINGS = Settings(delay_seconds=0, cache_hours=0, max_retries=1)


def make_config(site, *paths):
    return Config(settings=SETTINGS, products=[Product(url=site.url + p) for p in paths])


def test_two_runs_record_price_drop_and_keep_going_after_errors(site, tmp_path):
    site.pages["/kettle"] = (200, jsonld_page("Kettle", "40.00"))
    site.pages["/toaster"] = (200, jsonld_page("Toaster", "25.00", "OutOfStock"))
    site.pages["/broken"] = (200, "<html><body>no price here</body></html>")
    config = make_config(site, "/kettle", "/broken", "/toaster")
    db = tmp_path / "prices.db"

    with Store(db) as store:
        first = run(config, store, Fetcher(SETTINGS), now="2026-01-01T09:00:00+00:00")
        assert (first.ok, first.failed, first.changes) == (2, 1, [])

        site.pages["/kettle"] = (200, jsonld_page("Kettle", "32.00"))
        site.pages["/toaster"] = (200, jsonld_page("Toaster", "25.00", "InStock"))
        second = run(config, store, Fetcher(SETTINGS), now="2026-01-02T09:00:00+00:00")

    found = {(title, c.kind, c.pct_change) for _, title, c in second.changes}
    assert found == {("Kettle", "price_drop", -20.0), ("Toaster", "back_in_stock", None)}

    conn = sqlite3.connect(db)
    statuses = conn.execute("SELECT status, COUNT(*) FROM observations GROUP BY status").fetchall()
    assert dict(statuses) == {"ok": 4, "error": 2}
    assert conn.execute("SELECT price_cents FROM observations WHERE title='Kettle' ORDER BY observed_at").fetchall() == [(4000,), (3200,)]

    paths = export(db, tmp_path / "out")
    assert {p.name for p in paths} == {"latest.csv", "history.csv", "events.csv", "price_report.xlsx", "price_history.png"}
    latest = (tmp_path / "out" / "latest.csv").read_text()
    assert "Kettle,32.00,USD,yes,32.00,40.00" in latest


def test_robots_disallow_skips_page(site, tmp_path):
    site.pages["/robots.txt"] = (200, "User-agent: *\nDisallow: /private\n")
    site.pages["/private/item"] = (200, jsonld_page("Secret", "1.00"))
    with Store(tmp_path / "p.db") as store:
        summary = run(make_config(site, "/private/item"), store, Fetcher(SETTINGS))
    assert summary.failed == 1 and "robots.txt" in summary.errors[0][1]
    assert "/private/item" not in site.hits


def test_cache_avoids_second_request(site, tmp_path):
    site.pages["/kettle"] = (200, jsonld_page("Kettle", "40.00"))
    cached = Settings(delay_seconds=0, cache_hours=1)
    fetcher = Fetcher(cached, cache_dir=tmp_path / "cache")
    fetcher.get(site.url + "/kettle")
    Fetcher(cached, cache_dir=tmp_path / "cache").get(site.url + "/kettle")
    assert site.hits["/kettle"] == 1


def test_http_error_is_recorded(site, tmp_path):
    with Store(tmp_path / "p.db") as store:
        summary = run(make_config(site, "/gone"), store, Fetcher(SETTINGS))
    assert summary.failed == 1 and "HTTP 404" in summary.errors[0][1]
