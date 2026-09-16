"""The fault switch: the injector arms one or more faults here, and the next normal run suffers them.

A fault is applied inside the real ingestion path: at the download for the source faults, after
the download and before the files are written for the data faults. Everything the agent reads
afterwards (journal, raw files, warehouse) therefore looks like a real incident, and nothing in
the journal says that a fault was injected.
"""

from __future__ import annotations

import json
import random
import urllib.error
from datetime import date
from email.message import Message
from pathlib import Path
from typing import Callable

from pipeline.ingest import SOURCE_URL, check_archive, fetch_archive

SWITCH = Path("data/faults/active.json")
BACKUP_DIR = Path("data/faults/backup")
SEED = 0
# What the source serves for a day it has not published yet: 200 with an HTML page.
MISSING_DAY_PAGE = (b'<!DOCTYPE html><html lang="fr"><head><title>Prix des carburants en France, '
                    b'site gouvernemental</title></head><body></body></html>')

# {"prices.csv": (columns, rows), "stations.csv": (columns, rows)}, as the ingestion builds it.
Tables = dict[str, tuple[list[str], list[dict]]]


def deliver_as_is(tables: Tables, params: dict) -> Tables:
    """No fault: the day is delivered as published. A healthy run goes through exactly the same
    path as an injected one, so a false alarm cannot be told apart by anything technical."""
    return tables


def rename_price_column(tables: Tables, params: dict) -> Tables:
    """Schema drift: the source renames prix_valeur. The values themselves are untouched."""
    new_name = params.get("new_name", "valeur")
    columns, rows = tables["prices.csv"]
    tables["prices.csv"] = (
        [new_name if c == "prix_valeur" else c for c in columns],
        [{(new_name if k == "prix_valeur" else k): v for k, v in row.items()} for row in rows],
    )
    return tables


def blank_prices(tables: Tables, params: dict) -> Tables:
    """Null spike: a share of the prices arrives empty. Seeded, so the same rows every time."""
    columns, rows = tables["prices.csv"]
    count = round(len(rows) * params.get("fraction", 0.3))
    chosen = set(random.Random(SEED).sample(range(len(rows)), count))
    tables["prices.csv"] = (columns, [{**row, "prix_valeur": ""} if i in chosen else row
                                      for i, row in enumerate(rows)])
    return tables


def duplicate_prices(tables: Tables, params: dict) -> Tables:
    """Duplicates: a rerun that was not idempotent appended the whole day a second time."""
    columns, rows = tables["prices.csv"]
    tables["prices.csv"] = (columns, rows + [dict(row) for row in rows])
    return tables


def scale_prices(tables: Tables, params: dict) -> Tables:
    """Unit drift: prices arrive in thousandths of a euro (2.259 becomes 2259), the format this
    source used in 2019. Nothing breaks: the values are still valid numbers."""
    factor = params.get("factor", 1000)
    columns, rows = tables["prices.csv"]
    tables["prices.csv"] = (columns, [
        {**row, "prix_valeur": format(float(row["prix_valeur"]) * factor, "g")}
        if row["prix_valeur"] else row
        for row in rows
    ])
    return tables


def drop_half_the_stations(tables: Tables, params: dict) -> Tables:
    """Outside the six families: a truncated delivery, half of the stations missing with their
    prices. No test fails, only the volume shows it. Seeded, so the same stations every time."""
    station_columns, stations = tables["stations.csv"]
    price_columns, prices = tables["prices.csv"]
    ids = sorted({s["pdv_id"] for s in stations})
    dropped = set(random.Random(SEED).sample(ids, round(len(ids) * params.get("share", 0.5))))
    tables["stations.csv"] = (station_columns, [s for s in stations if s["pdv_id"] not in dropped])
    tables["prices.csv"] = (price_columns, [p for p in prices if p["pdv_id"] not in dropped])
    return tables


# Faults added at J22, after the baseline rules were frozen: the benchmark's "unseen" scenarios.

def scale_one_fuel(tables: Tables, params: dict) -> Tables:
    """Unit drift on one product: the source moves one fuel to thousandths of a euro before the
    others. Every other fuel is untouched, and nothing breaks."""
    fuel, factor = params.get("fuel", "E85"), params.get("factor", 1000)
    columns, rows = tables["prices.csv"]
    tables["prices.csv"] = (columns, [
        {**row, "prix_valeur": format(float(row["prix_valeur"]) * factor, "g")}
        if row["prix_nom"] == fuel and row["prix_valeur"] else row
        for row in rows
    ])
    return tables


def duplicate_some_stations(tables: Tables, params: dict) -> Tables:
    """Partial duplicates: the source sends its file in chunks and one chunk twice, so the prices
    of a share of the stations arrive a second time. Seeded, so the same stations every time."""
    columns, rows = tables["prices.csv"]
    ids = sorted({row["pdv_id"] for row in rows})
    chosen = set(random.Random(SEED).sample(ids, round(len(ids) * params.get("share", 0.1))))
    tables["prices.csv"] = (columns, rows + [dict(row) for row in rows if row["pdv_id"] in chosen])
    return tables


def zero_some_prices(tables: Tables, params: dict) -> Tables:
    """Outside the six families: a share of the prices arrives as 0 instead of empty, a placeholder
    the source's export writes for a missing value. Nothing is empty and no test fails. Seeded."""
    columns, rows = tables["prices.csv"]
    filled = [i for i, row in enumerate(rows) if row["prix_valeur"]]
    chosen = set(random.Random(SEED).sample(filled, round(len(filled) * params.get("share", 0.02))))
    tables["prices.csv"] = (columns, [{**row, "prix_valeur": "0"} if i in chosen else row
                                      for i, row in enumerate(rows)])
    return tables


def drop_a_region(tables: Tables, params: dict) -> Tables:
    """Outside the six families: every station of one region is missing with its prices, as if a
    regional feed were lost upstream. The departments default to Brittany. No test fails."""
    departments = tuple(params.get("departments", ("22", "29", "35", "56")))
    station_columns, stations = tables["stations.csv"]
    price_columns, prices = tables["prices.csv"]
    dropped = {s["pdv_id"] for s in stations if s["cp"][:2] in departments}
    tables["stations.csv"] = (station_columns, [s for s in stations if s["pdv_id"] not in dropped])
    tables["prices.csv"] = (price_columns, [p for p in prices if p["pdv_id"] not in dropped])
    return tables


def raise_all_prices(tables: Tables, params: dict) -> Tables:
    """No fault: every price really rose overnight by the same share, as after a tax change. A large
    change in the data, and nothing to repair."""
    factor = params.get("factor", 1.08)
    columns, rows = tables["prices.csv"]
    tables["prices.csv"] = (columns, [
        {**row, "prix_valeur": format(round(float(row["prix_valeur"]) * factor, 3), "g")}
        if row["prix_valeur"] else row
        for row in rows
    ])
    return tables


def _missing_archive(day: date) -> bytes:
    # Goes through the real check, so the error reads exactly like a real missing day.
    return check_archive(day, MISSING_DAY_PAGE, "text/html")


def _server_error(day: date) -> bytes:
    raise urllib.error.HTTPError(SOURCE_URL.format(day=day), 500, "Internal Server Error",
                                 Message(), None)


TAMPERS: dict[str, Callable[[Tables, dict], Tables]] = {
    "healthy": deliver_as_is,
    "schema_drift": rename_price_column,
    "null_spike": blank_prices,
    "duplicate_rows": duplicate_prices,
    "unit_drift": scale_prices,
    "truncated_delivery": drop_half_the_stations,
    "unit_drift_one_fuel": scale_one_fuel,
    "partial_duplicates": duplicate_some_stations,
    "zero_prices": zero_some_prices,
    "missing_region": drop_a_region,
    "price_rise": raise_all_prices,
}
SOURCE_FAULTS: dict[str, Callable[[date], bytes]] = {
    "freshness": _missing_archive,
    "source_error": _server_error,
}


def armed(switch: Path = SWITCH) -> dict | None:
    return json.loads(switch.read_text(encoding="utf-8")) if switch.exists() else None


def armed_faults(fault: dict) -> list[dict]:
    """The faults held by the switch, as a list of {"name", "params"} (one or several)."""
    if "faults" in fault:
        return fault["faults"]
    return [{"name": fault["name"], "params": fault.get("params", {})}]


def _targets(day: date, fault: dict | None) -> bool:
    return fault is not None and fault["date"] == day.isoformat()


def fetch_for(day: date, fault: dict | None, backup_dir: Path = BACKUP_DIR) -> Callable[[date], bytes]:
    """Normally the network. While faults are armed for this day: a failing source if one of them
    is a source fault (on every rerun, until --reset; nothing else can then arrive), otherwise the
    backed-up clean archive, so an injected run needs no network and gives the same result."""
    if not _targets(day, fault):
        return fetch_archive
    for armed_fault in armed_faults(fault):
        if armed_fault["name"] in SOURCE_FAULTS:
            return SOURCE_FAULTS[armed_fault["name"]]
    clean = backup_dir / fault["date"] / "source.zip"
    return lambda _day: clean.read_bytes()


def tamper_for(day: date, fault: dict | None) -> Callable[[Tables], Tables] | None:
    """The data faults armed for this day, applied one after the other in the order given."""
    if not _targets(day, fault):
        return None
    steps = [(TAMPERS[f["name"]], f.get("params") or {}) for f in armed_faults(fault)
             if f["name"] in TAMPERS]
    if not steps:
        return None

    def tamper(tables: Tables) -> Tables:
        for apply, params in steps:
            tables = apply(tables, params)
        return tables
    return tamper
