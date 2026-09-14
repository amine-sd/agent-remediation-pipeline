"""The fault switch: the injector arms a fault here, and the next normal run suffers it.

A fault is applied inside the real ingestion path: at the download for the source faults,
after the download and before the files are written for the data faults. Everything the agent
reads afterwards (journal, raw files, warehouse) therefore looks like a real incident, and
nothing in the journal says that a fault was injected.
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


def _missing_archive(day: date) -> bytes:
    # Goes through the real check, so the error reads exactly like a real missing day.
    return check_archive(day, MISSING_DAY_PAGE, "text/html")


def _server_error(day: date) -> bytes:
    raise urllib.error.HTTPError(SOURCE_URL.format(day=day), 500, "Internal Server Error",
                                 Message(), None)


TAMPERS: dict[str, Callable[[Tables, dict], Tables]] = {
    "schema_drift": rename_price_column,
    "null_spike": blank_prices,
    "duplicate_rows": duplicate_prices,
    "unit_drift": scale_prices,
}
SOURCE_FAULTS: dict[str, Callable[[date], bytes]] = {
    "freshness": _missing_archive,
    "source_error": _server_error,
}


def armed(switch: Path = SWITCH) -> dict | None:
    return json.loads(switch.read_text(encoding="utf-8")) if switch.exists() else None


def _targets(day: date, fault: dict | None) -> bool:
    return fault is not None and fault["date"] == day.isoformat()


def fetch_for(day: date, fault: dict | None, backup_dir: Path = BACKUP_DIR) -> Callable[[date], bytes]:
    """Normally the network. While a fault is armed for this day: a failing source for the
    source faults (on every rerun, until --reset), otherwise the backed-up clean archive, so an
    injected run needs no network and gives the same result every time."""
    if not _targets(day, fault):
        return fetch_archive
    if fault["name"] in SOURCE_FAULTS:
        return SOURCE_FAULTS[fault["name"]]
    clean = backup_dir / fault["date"] / "source.zip"
    return lambda _day: clean.read_bytes()


def tamper_for(day: date, fault: dict | None) -> Callable[[Tables], Tables] | None:
    if not _targets(day, fault) or fault["name"] not in TAMPERS:
        return None
    tamper, params = TAMPERS[fault["name"]], fault.get("params", {})
    return lambda tables: tamper(tables, params)
