"""Gather everything the agent can read about the last pipeline run, in one dictionary.

Each section is collected on its own. When the pipeline is broken, some sources are missing
(no warehouse, no manifest), and that absence is itself information: a section that cannot
be read carries an "error" key instead of making the whole collection fail.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Callable

import duckdb

from pipeline.ingest import RAW_DIR
from pipeline.run import DBT_PROJECT, JOURNAL

WAREHOUSE = Path("data/warehouse.duckdb")
MANIFEST = DBT_PROJECT / "target" / "manifest.json"
FCT_COLUMNS = ["snapshot_date", "station_id", "fuel_id", "fuel_name", "price_updated_at",
               "price_eur_per_liter"]


def _section(read: Callable[[], dict]) -> dict:
    try:
        return read()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def read_journal(journal: Path = JOURNAL) -> list[dict]:
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _step(entry: dict) -> dict:
    step = {key: entry.get(key) for key in ("step", "status", "started_at", "finished_at", "error")}
    # A step that raised has no output worth showing, only a traceback kept for humans; older
    # journal lines stored that traceback as output, hence the guard.
    step["output"] = None if entry.get("error") else entry.get("output")
    return step


def last_run(entries: list[dict]) -> dict:
    run_id = entries[-1]["run_id"]
    steps = [e for e in entries if e["run_id"] == run_id]
    return {
        "run_id": run_id,
        "mode": steps[0]["mode"],
        "data_date": steps[0]["data_date"],
        "status": "failed" if any(e["status"] == "failed" for e in steps) else "success",
        "steps": [_step(e) for e in steps],
    }


def dbt_results(entries: list[dict]) -> dict:
    """Per-node results of the most recent transform and test steps, whichever run they belong to."""
    latest = {}
    for e in entries:
        if e["step"] in ("transform", "test") and e["status"] != "skipped":
            latest[e["step"]] = {"run_id": e["run_id"],
                                 "results": (e.get("details") or {}).get("dbt_results", [])}
    return latest


def _short(unique_id: str) -> str:
    # model.fuel_prices.fct_prices -> fct_prices ; source.fuel_prices.raw.prices -> source:raw.prices
    kind, _project, *name = unique_id.split(".")
    return f"source:{'.'.join(name)}" if kind == "source" else ".".join(name)


def lineage(manifest: Path = MANIFEST) -> dict:
    """Each model and the models or sources it reads from. Tests are left out."""
    parent_map = json.loads(manifest.read_text(encoding="utf-8"))["parent_map"]
    return {_short(child): sorted(_short(parent) for parent in parents)
            for child, parents in parent_map.items() if child.startswith("model.")}


def raw_files(data_date: str, raw_dir: Path = RAW_DIR) -> dict:
    """What landed for a data date: which files, which columns, how many rows."""
    folder = raw_dir / data_date
    if not folder.exists():
        return {"folder": folder.as_posix(), "exists": False}
    files = {}
    for name in ("stations.csv", "prices.csv"):
        path = folder / name
        if not path.exists():
            files[name] = None
            continue
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            files[name] = {"columns": header, "rows": sum(1 for _ in reader)}
    return {"folder": folder.as_posix(), "exists": True, "files": files}


def day_stats(con: duckdb.DuckDBPyConnection, day: date) -> dict:
    """Volumes, empty values per column and average price per fuel for one snapshot day."""
    null_counts = ", ".join(f"count(*) - count({c})" for c in FCT_COLUMNS)
    prices, stations = con.execute(
        "select count(*), count(distinct station_id) from fct_prices where snapshot_date = ?",
        [day]).fetchone()
    nulls = con.execute(f"select {null_counts} from fct_prices where snapshot_date = ?",
                        [day]).fetchone()
    averages = con.execute(
        "select fuel_name, avg(price_eur_per_liter) from fct_prices "
        "where snapshot_date = ? group by 1 order by 1", [day]).fetchall()
    return {
        "prices": prices,
        "stations": stations,
        "null_counts": dict(zip(FCT_COLUMNS, nulls)),
        "avg_price_by_fuel": {fuel: None if avg is None else round(avg, 4) for fuel, avg in averages},
    }


def warehouse_stats(warehouse: Path = WAREHOUSE, days: int = 2) -> dict:
    """Statistics of the most recent snapshot days, so a day can be compared with the one before."""
    if not warehouse.exists():
        raise FileNotFoundError(warehouse.as_posix())
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        dates = [d for (d,) in con.execute(
            "select distinct snapshot_date from fct_prices order by 1 desc limit ?", [days]).fetchall()]
        by_day = {day.isoformat(): day_stats(con, day) for day in dates}
    finally:
        con.close()
    return {"latest_snapshot_date": dates[0].isoformat() if dates else None, "by_day": by_day}


def collect_context(journal: Path = JOURNAL, warehouse: Path = WAREHOUSE,
                    manifest: Path = MANIFEST, raw_dir: Path = RAW_DIR) -> dict:
    run = _section(lambda: last_run(read_journal(journal)))
    return {
        "last_run": run,
        "dbt_results": _section(lambda: dbt_results(read_journal(journal))),
        "raw_files": _section(lambda: raw_files(run["data_date"], raw_dir)),
        "lineage": _section(lambda: lineage(manifest)),
        "warehouse_stats": _section(lambda: warehouse_stats(warehouse)),
    }


if __name__ == "__main__":
    print(json.dumps(collect_context(), indent=2, ensure_ascii=False))
