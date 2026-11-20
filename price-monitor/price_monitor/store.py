"""SQLite storage for products, price observations and change events.

Prices are stored as integer cents so comparisons are exact.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    name         TEXT,
    target_cents INTEGER,
    first_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    observed_at TEXT NOT NULL,          -- UTC ISO 8601
    status      TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    price_cents INTEGER,
    currency    TEXT,
    in_stock    INTEGER,                -- 1, 0 or NULL when unknown
    title       TEXT,
    method      TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_product_time ON observations(product_id, observed_at);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    observed_at TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- price_drop, price_rise, back_in_stock, out_of_stock, target_hit
    old_cents   INTEGER,
    new_cents   INTEGER,
    pct_change  REAL
);
"""


def to_cents(value: Decimal | None) -> int | None:
    return None if value is None else int((value * 100).to_integral_value())


def from_cents(value: int | None) -> Decimal | None:
    return None if value is None else (Decimal(value) / 100).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Observation:
    observed_at: str
    price_cents: int
    in_stock: bool | None


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_product(self, url: str, name: str | None, target: Decimal | None, now: str) -> int:
        self.conn.execute(
            """
            INSERT INTO products (url, name, target_cents, first_seen) VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                name = COALESCE(excluded.name, products.name),
                target_cents = excluded.target_cents
            """,
            (url, name, to_cents(target), now),
        )
        return self.conn.execute("SELECT id FROM products WHERE url = ?", (url,)).fetchone()[0]

    def last_ok(self, product_id: int, before: str) -> Observation | None:
        row = self.conn.execute(
            """
            SELECT observed_at, price_cents, in_stock FROM observations
            WHERE product_id = ? AND status = 'ok' AND observed_at < ?
            ORDER BY observed_at DESC LIMIT 1
            """,
            (product_id, before),
        ).fetchone()
        if row is None:
            return None
        return Observation(row[0], row[1], None if row[2] is None else bool(row[2]))

    def add_observation(self, product_id: int, now: str, **fields) -> None:
        cols = ["product_id", "observed_at", *fields]
        self.conn.execute(
            f"INSERT INTO observations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (product_id, now, *fields.values()),
        )

    def add_event(self, product_id: int, now: str, kind: str, old: int | None, new: int | None, pct: float | None) -> None:
        self.conn.execute(
            "INSERT INTO events (product_id, observed_at, kind, old_cents, new_cents, pct_change) VALUES (?, ?, ?, ?, ?, ?)",
            (product_id, now, kind, old, new, pct),
        )

    def commit(self) -> None:
        self.conn.commit()
