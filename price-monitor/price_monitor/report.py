"""Export the price history to CSV and Excel, and chart it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

LATEST_SQL = """
WITH ranked AS (
    SELECT o.*, ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY observed_at DESC) AS rn
    FROM observations o WHERE status = 'ok'
),
stats AS (
    SELECT product_id, MIN(price_cents) AS low_cents, MAX(price_cents) AS high_cents, COUNT(*) AS checks
    FROM observations WHERE status = 'ok' GROUP BY product_id
),
last_error AS (
    SELECT product_id, MAX(observed_at) AS last_error_at FROM observations
    WHERE status = 'error' GROUP BY product_id
)
SELECT p.id AS product_id,
       COALESCE(p.name, r.title) AS product,
       r.price_cents / 100.0 AS price,
       r.currency,
       CASE r.in_stock WHEN 1 THEN 'yes' WHEN 0 THEN 'no' END AS in_stock,
       s.low_cents / 100.0 AS lowest_seen,
       s.high_cents / 100.0 AS highest_seen,
       p.target_cents / 100.0 AS target_price,
       s.checks,
       r.observed_at AS last_checked,
       e.last_error_at,
       p.url
FROM products p
LEFT JOIN ranked r ON r.product_id = p.id AND r.rn = 1
LEFT JOIN stats s ON s.product_id = p.id
LEFT JOIN last_error e ON e.product_id = p.id
ORDER BY product
"""

HISTORY_SQL = """
SELECT o.observed_at, COALESCE(p.name, o.title) AS product, o.status,
       o.price_cents / 100.0 AS price, o.currency,
       CASE o.in_stock WHEN 1 THEN 'yes' WHEN 0 THEN 'no' END AS in_stock,
       o.method, o.error, p.url
FROM observations o JOIN products p ON p.id = o.product_id
ORDER BY o.observed_at, product
"""

EVENTS_SQL = """
SELECT e.observed_at, COALESCE(p.name, (SELECT title FROM observations
           WHERE product_id = p.id AND title IS NOT NULL ORDER BY observed_at DESC LIMIT 1)) AS product,
       e.kind, e.old_cents / 100.0 AS old_price, e.new_cents / 100.0 AS new_price,
       e.pct_change, p.url
FROM events e JOIN products p ON p.id = e.product_id
ORDER BY e.observed_at DESC, product
"""


def load_frames(db_path: str | Path) -> dict[str, pd.DataFrame]:
    with sqlite3.connect(db_path) as conn:
        return {
            "latest": pd.read_sql_query(LATEST_SQL, conn).drop(columns="product_id"),
            "history": pd.read_sql_query(HISTORY_SQL, conn),
            "events": pd.read_sql_query(EVENTS_SQL, conn),
        }


def export(db_path: str | Path, out_dir: str | Path, chart: bool = True) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = load_frames(db_path)
    written: list[Path] = []

    for name, df in frames.items():
        path = out / f"{name}.csv"
        df.to_csv(path, index=False, float_format="%.2f")
        written.append(path)

    xlsx = out / "price_report.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        for name, df in frames.items():
            df.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            for col in sheet.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                sheet.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)
    written.append(xlsx)

    if chart and not frames["history"].empty:
        written.append(_chart(frames["history"], frames["events"], out / "price_history.png"))
    return written


def _chart(history: pd.DataFrame, events: pd.DataFrame, path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ok = history[history["status"] == "ok"].copy()
    ok["observed_at"] = pd.to_datetime(ok["observed_at"])
    drops = events[events["kind"] == "price_drop"].copy()
    drops["observed_at"] = pd.to_datetime(drops["observed_at"])

    products = sorted(ok["product"].dropna().unique())
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    for product in products:
        series = ok[ok["product"] == product].sort_values("observed_at")
        label = product if len(product) <= 40 else product[:37] + "..."
        ax.step(series["observed_at"], series["price"], where="post", marker="o", markersize=3, label=label)
        d = drops[drops["product"] == product]
        if not d.empty:
            ax.scatter(d["observed_at"], d["new_price"], s=70, facecolors="none", edgecolors="crimson", zorder=3)

    ax.set_title("Price history (red circles mark price drops)")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
