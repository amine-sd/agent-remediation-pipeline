"""Rebuild the benchmark's data from the archives shipped in the repository, without network.

    python -m bench.bootstrap

The source deletes its daily archives after about thirty days, so the four days the scenarios use
are shipped in bench/days/ (Licence Ouverte Etalab, see bench/days/SOURCE.md). Each archive goes
through the real ingestion, then dbt builds the warehouse. Days already present are left as they are.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pipeline.ingest import RAW_DIR, ingest_day
from pipeline.run import run_dbt

DAYS = Path(__file__).parent / "days"


def bootstrap(days_dir: Path = DAYS, raw_dir: Path = RAW_DIR, build: bool = True) -> list[dict]:
    summaries = []
    for archive in sorted(days_dir.glob("*.zip")):
        day = date.fromisoformat(archive.stem)
        summaries.append(ingest_day(day, raw_dir=raw_dir, fetch=lambda _day, a=archive: a.read_bytes()))
    if build:
        result = run_dbt("build")
        if not result["ok"]:
            raise RuntimeError("dbt build failed:\n" + result["output"])
    return summaries


if __name__ == "__main__":
    for summary in bootstrap():
        print(summary)
    print("warehouse built")
