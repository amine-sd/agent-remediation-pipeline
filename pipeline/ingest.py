"""Download one day of fuel prices and land it in data/raw/YYYY-MM-DD/.

The step is idempotent: if the day's folder already exists, nothing is downloaded or
written. A day is first written to a temporary folder, then renamed, so an interrupted
run never leaves a half-written day behind.
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

SOURCE_URL = "https://donnees.roulez-eco.fr/opendata/jour/{day:%Y%m%d}"
RAW_DIR = Path("data/raw")
USER_AGENT = "agent-remediation-pipeline/0.1"
RENAME_ATTEMPTS = 5

STATION_COLUMNS = ["pdv_id", "latitude", "longitude", "cp", "pop", "adresse", "ville",
                   "automate_24_24", "services"]
PRICE_COLUMNS = ["pdv_id", "prix_id", "prix_nom", "prix_maj", "prix_valeur"]


class SourceUnavailable(Exception):
    """The source answered, but the day's archive is not there (not published yet, or expired)."""


def fetch_archive(day: date) -> bytes:
    request = urllib.request.Request(SOURCE_URL.format(day=day), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
        content_type = response.headers.get_content_type()
    return check_archive(day, payload, content_type)


def check_archive(day: date, payload: bytes, content_type: str) -> bytes:
    # For a missing day the source answers 200 with an HTML page, so the status is not
    # enough: check the zip signature.
    if not payload.startswith(b"PK\x03\x04"):
        raise SourceUnavailable(f"no archive for {day}: got {len(payload)} bytes of {content_type}")
    return payload


def parse_archive(archive: bytes) -> tuple[list[dict], list[dict]]:
    """Flatten the XML into one row per station and one row per (station, fuel) price.

    Values are kept as the source writes them (text, source names): the raw layer mirrors
    the source, and typing happens in dbt staging.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        (xml_name,) = [name for name in zf.namelist() if name.endswith(".xml")]
        root = ET.fromstring(zf.read(xml_name))

    stations, prices = [], []
    for pdv in root.iter("pdv"):
        pdv_id = pdv.get("id", "")
        horaires = pdv.find("horaires")
        stations.append({
            "pdv_id": pdv_id,
            "latitude": pdv.get("latitude", ""),
            "longitude": pdv.get("longitude", ""),
            "cp": pdv.get("cp", ""),
            "pop": pdv.get("pop", ""),
            "adresse": pdv.findtext("adresse", ""),
            "ville": pdv.findtext("ville", ""),
            "automate_24_24": horaires.get("automate-24-24", "") if horaires is not None else "",
            "services": "|".join(s.text or "" for s in pdv.iterfind("services/service")),
        })
        for prix in pdv.iterfind("prix"):
            prices.append({
                "pdv_id": pdv_id,
                "prix_id": prix.get("id", ""),
                "prix_nom": prix.get("nom", ""),
                "prix_maj": prix.get("maj", ""),
                "prix_valeur": prix.get("valeur", ""),
            })
    return stations, prices


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _rename_with_retry(source: Path, target: Path) -> None:
    # On Windows, a process scanning the files just written (an antivirus, the indexer) can
    # lock the folder for a moment, and the rename fails with PermissionError. Seen once in
    # practice: a short retry absorbs it, and a lock that lasts still raises.
    for attempt in range(1, RENAME_ATTEMPTS + 1):
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == RENAME_ATTEMPTS:
                raise
            time.sleep(0.5 * attempt)


def ingest_day(day: date, raw_dir: Path = RAW_DIR,
               fetch: Callable[[date], bytes] = fetch_archive,
               tamper: Callable[[dict], dict] | None = None) -> dict:
    """Land one day and return a short summary. Raises if the source fails.

    `fetch` and `tamper` are parameters so that tests and the fault injector can replace the
    network call, or alter what the source delivered, without touching this function. In a
    normal run `tamper` is None.
    """
    target = raw_dir / day.isoformat()
    if target.exists():
        return {"date": day.isoformat(), "status": "skipped", "reason": "already ingested"}

    archive = fetch(day)
    stations, prices = parse_archive(archive)
    tables = {"stations.csv": (STATION_COLUMNS, stations), "prices.csv": (PRICE_COLUMNS, prices)}
    if tamper is not None:
        tables = tamper(tables)

    # Two levels deep, so the dbt glob data/raw/*/prices.csv never sees a partial day.
    tmp = raw_dir / ".tmp" / day.isoformat()
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    (tmp / "source.zip").write_bytes(archive)
    for name, (columns, rows) in tables.items():
        _write_csv(tmp / name, columns, rows)
    _rename_with_retry(tmp, target)
    return {"date": day.isoformat(), "status": "ingested",
            "stations": len(tables["stations.csv"][1]), "prices": len(tables["prices.csv"][1])}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Land one day of fuel prices in data/raw/.")
    parser.add_argument("--date", type=date.fromisoformat,
                        default=date.today() - timedelta(days=1),
                        help="data date, YYYY-MM-DD (default: yesterday)")
    args = parser.parse_args(argv)
    print(ingest_day(args.date))


if __name__ == "__main__":
    main()
