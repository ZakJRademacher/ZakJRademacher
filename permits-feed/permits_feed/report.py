"""Build the weekly spreadsheet, CSVs and chart from the database."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font

from .config import Config


def load(db_path: str | Path, config: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(db_path) as conn:
        records = pd.read_sql_query(
            "SELECT id, data, first_seen_run, last_changed_run FROM records ORDER BY record_date DESC, id", conn
        )
        runs = pd.read_sql_query("SELECT * FROM runs ORDER BY run_id", conn)

    columns = list(config.fields)
    if records.empty:
        frame = pd.DataFrame(columns=["id", *columns, "first_seen_run", "last_changed_run"])
    else:
        data = pd.DataFrame([json.loads(d) for d in records["data"]], columns=columns)
        frame = pd.concat([records[["id"]], data, records[["first_seen_run", "last_changed_run"]]], axis=1)
    for column, kind in config.types.items():
        if kind == "int":
            frame[column] = frame[column].astype("Int64")  # whole numbers stay whole when some are blank
        elif kind in ("money", "float"):
            frame[column] = frame[column].astype("Float64")

    if config.report.address:
        parts = frame[config.report.address].astype("string").fillna("")
        address = parts.apply(lambda r: " ".join(p for p in r if p), axis=1) if not frame.empty else pd.Series(dtype="string")
        frame.insert(1, "address", address)
    return frame, runs


def summarize(new: pd.DataFrame, config: Config) -> dict[str, pd.DataFrame]:
    value = config.report.value_field
    out = {}
    for col in config.report.group_by:
        agg = {"records": ("id", "count")}
        if value:
            agg.update({f"total_{value}": (value, "sum"), f"median_{value}": (value, "median")})
        table = new.groupby(col, dropna=False).agg(**agg).sort_values("records", ascending=False).reset_index()
        out[f"by_{col}"] = table
    return out


def export(db_path: str | Path, out_dir: str | Path, config: Config, chart: bool = True) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records, runs = load(db_path, config)

    ok_runs = runs[runs["status"] == "ok"]
    last_run = int(ok_runs["run_id"].max()) if not ok_runs.empty else None
    new = records[records["first_seen_run"] == last_run].drop(columns=["first_seen_run", "last_changed_run"])
    run_date = str(ok_runs["ran_at"].iloc[-1])[:10] if last_run else "no-runs"

    summaries = summarize(new, config) if not new.empty else {}
    written: list[Path] = []

    new_csv = out / "new_records.csv"
    new.to_csv(new_csv, index=False)
    all_csv = out / "all_records.csv"
    records.to_csv(all_csv, index=False)
    written += [new_csv, all_csv]

    xlsx = out / f"{config.source.dataset}_{run_date}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        sheets = {"new_this_run": new, **summaries, "all_records": records, "runs": runs}
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
            sheet = writer.sheets[name[:31]]
            sheet.freeze_panes = "A2"
            if len(df.columns):
                sheet.auto_filter.ref = sheet.dimensions
            for col in sheet.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                sheet.column_dimensions[col[0].column_letter].width = min(max(width + 2, 9), 60)
            for cell in sheet[1]:
                cell.font = Font(bold=True)
    written.append(xlsx)

    if chart and not records.empty:
        written.append(_chart(records, config, run_date, out / "weekly_trend.png"))
    return written


def _chart(records: pd.DataFrame, config: Config, run_date: str, path: Path) -> Path:
    """Weekly totals. Only full weeks are drawn; recent weeks that may still
    receive late-posted records are drawn lighter."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    date_col = config.date_column
    value = config.report.value_field
    df = records.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    df["week"] = df[date_col].dt.to_period("W-SUN").dt.start_time

    weekly = df.groupby("week").agg(records=("id", "count"))
    if value:
        weekly["value"] = df.groupby("week")[value].sum().astype(float) / 1e6

    start = pd.Timestamp(config.source.start_date)
    last_run = pd.Timestamp(run_date)
    full = (weekly.index >= start) & (weekly.index + pd.Timedelta(days=7) <= last_run)
    weekly = weekly[full]
    settled = weekly.index + pd.Timedelta(days=7 + config.source.lookback_days) <= last_run

    panels = 2 if value else 1
    fig, axes = plt.subplots(panels, 1, figsize=(10, 3.2 * panels), dpi=150, sharex=True)
    axes = [axes] if panels == 1 else list(axes)
    labels = weekly.index.strftime("%b %d")
    series = [("records", "Records", "#3b6ea5", f"{config.source.name}: records per week")]
    if value:
        series.append(("value", f"{value} ($M)", "#c47a2c", f"Total {value.replace('_', ' ')} per week"))
    for ax, (col, ylabel, color, title) in zip(axes, series):
        ax.bar(labels, weekly[col], color=[color if ok else color + "66" for ok in settled])
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)
        ax.set_axisbelow(True)
    if not settled.all():
        axes[0].text(1, 1.02, "lighter bars: late records may still arrive", transform=axes[0].transAxes,
                     ha="right", va="bottom", fontsize=8, color="#555555")
    axes[-1].set_xlabel("Week starting (full weeks only)")
    plt.setp(axes[-1].get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
