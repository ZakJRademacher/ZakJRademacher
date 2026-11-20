"""Generate sample_output/ by running the real monitor against a simulated store.

books.toscrape.com never changes its prices, so it can't show drops. This script
serves a small fake store on localhost whose prices and stock change day by day,
then runs the normal fetch -> extract -> store -> alert pipeline once per "day"
for 21 days and exports the reports.

    python scripts/demo.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from price_monitor.config import Config, Product, Settings  # noqa: E402
from price_monitor.fetch import Fetcher  # noqa: E402
from price_monitor.monitor import run  # noqa: E402
from price_monitor.report import export  # noqa: E402
from price_monitor.store import Store, from_cents  # noqa: E402

DAYS = 21

# slug -> (name, list of (from_day, price, in_stock))
CATALOG = {
    "espresso-machine": ("Espresso Machine", [(0, 349.99, True), (6, 329.99, True), (13, 279.99, True), (17, 329.99, True)]),
    "noise-cancelling-headphones": ("Noise-Cancelling Headphones", [(0, 199.00, True), (4, 199.00, False), (9, 179.00, True)]),
    "standing-desk": ("Standing Desk", [(0, 499.00, True), (11, 449.00, True)]),
    "air-fryer": ("Air Fryer XL", [(0, 89.99, True), (7, 94.99, True), (15, 74.99, True)]),
    "trail-running-shoes": ("Trail Running Shoes", [(0, 129.95, True), (19, 128.95, True)]),  # <1% drop: ignored
}
TARGETS = {"standing-desk": 450.00}

state = {"day": 0}


def price_on(slug: str, day: int) -> tuple[float, bool]:
    current = CATALOG[slug][1][0]
    for step in CATALOG[slug][1]:
        if step[0] <= day:
            current = step
    return current[1], current[2]


def page(slug: str) -> str:
    name = CATALOG[slug][0]
    price, in_stock = price_on(slug, state["day"])
    availability = "InStock" if in_stock else "OutOfStock"
    return f"""<!doctype html><html><head><title>{name} | Demo Store</title>
<script type="application/ld+json">
{{"@context": "https://schema.org", "@type": "Product", "name": "{name}",
  "offers": {{"@type": "Offer", "price": "{price:.2f}", "priceCurrency": "USD",
             "availability": "https://schema.org/{availability}"}}}}
</script></head><body><h1>{name}</h1><p>${price:,.2f}</p></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        slug = self.path.strip("/").removeprefix("products/")
        if self.path == "/robots.txt":
            body, status = "User-agent: *\nAllow: /\n", 200
        elif slug in CATALOG:
            body, status = page(slug), 200
        else:
            body, status = "not found", 404
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/products/"

    settings = Settings(delay_seconds=0, cache_hours=0)
    products = [Product(url=base + slug, target_price=_dec(TARGETS.get(slug))) for slug in CATALOG]
    config = Config(settings=settings, products=products)

    out = ROOT / "sample_output"
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "prices.db"
        start = datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)
        with Store(db) as store:
            for day in range(DAYS):
                state["day"] = day
                now = (start + timedelta(days=day)).isoformat()
                summary = run(config, store, Fetcher(settings), now=now)
                for _, title, ch in summary.changes:
                    detail = f" {from_cents(ch.old_cents)} -> {from_cents(ch.new_cents)}" if ch.new_cents else ""
                    print(f"day {day:>2}  {ch.kind:<14} {title}{detail}")
        server.shutdown()

        # Store URLs point at a throwaway localhost port; make them readable.
        if out.exists():
            shutil.rmtree(out)
        out.mkdir()
        shutil.copy(db, out / "prices.db")
        _rewrite_db_urls(out / "prices.db", base)
        export(out / "prices.db", out)
    print(f"\nwrote sample output to {out}")


def _rewrite_db_urls(db: Path, base: str) -> None:
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE products SET url = replace(url, ?, 'https://demo-store.example/products/')", (base,))


def _dec(value):
    from decimal import Decimal

    return None if value is None else Decimal(str(value))


if __name__ == "__main__":
    main()
