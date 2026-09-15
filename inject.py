"""Inject one or several faults into the pipeline, or undo them.

    python inject.py --scenario 1 [--date 2026-09-13] [--fraction 0.3]
    python inject.py --reset

Injecting backs up the target day, takes it out of the raw layer and the warehouse, arms the
fault, then runs the pipeline for that day: the day is ingested again and suffers the fault
through the real code path, as a real daily run would. --reset puts the backed-up day back
and rebuilds the warehouse, so the pipeline is exactly as it was. Several faults can be armed
together on one day (the benchmark does it for its "two simultaneous faults" trap).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from pipeline import faults
from pipeline.ingest import RAW_DIR
from pipeline.run import run_dbt, run_pipeline

SCENARIOS = {1: "schema_drift", 2: "null_spike", 3: "duplicate_rows",
             4: "freshness", 5: "unit_drift", 6: "source_error", 7: "healthy",
             8: "truncated_delivery"}


def latest_day(raw_dir: Path = RAW_DIR) -> date:
    days = []
    for folder in raw_dir.iterdir():
        try:
            days.append(date.fromisoformat(folder.name))
        except ValueError:
            pass  # .tmp, or anything else that is not a day
    return max(days)


def rebuild_warehouse() -> None:
    result = run_dbt("run")
    if not result["ok"]:
        raise RuntimeError("dbt run failed while rebuilding the warehouse:\n" + result["output"])


def inject(to_arm: str | list[dict], day: date, params: dict | None = None,
           raw_dir: Path = RAW_DIR, switch: Path = faults.SWITCH,
           backup_dir: Path = faults.BACKUP_DIR,
           rebuild: Callable[[], None] = rebuild_warehouse) -> dict:
    """Arm one fault (a name, with `params`) or several (a list of {"name", "params"})."""
    if switch.exists():
        raise RuntimeError(f"a fault is already armed ({switch.as_posix()}); run --reset first")
    folder = raw_dir / day.isoformat()
    if not folder.exists():
        raise RuntimeError(f"{folder.as_posix()} does not exist: there is no ingested day to break")
    to_arm = ([{"name": to_arm, "params": params or {}}] if isinstance(to_arm, str)
              else [{"name": f["name"], "params": f.get("params") or {}} for f in to_arm])

    backup = backup_dir / day.isoformat()
    shutil.rmtree(backup, ignore_errors=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(backup))

    fault = {"faults": to_arm, "date": day.isoformat(),
             "armed_at": datetime.now().isoformat(timespec="seconds")}
    switch.parent.mkdir(parents=True, exist_ok=True)
    switch.write_text(json.dumps(fault, indent=2), encoding="utf-8")
    rebuild()
    return fault


def reset(raw_dir: Path = RAW_DIR, switch: Path = faults.SWITCH,
          backup_dir: Path = faults.BACKUP_DIR,
          rebuild: Callable[[], None] = rebuild_warehouse) -> str:
    fault = faults.armed(switch)
    if fault is None:
        return "no fault armed, nothing to reset"
    folder, backup = raw_dir / fault["date"], backup_dir / fault["date"]
    shutil.rmtree(folder, ignore_errors=True)  # what the faulty run wrote, if it ran
    shutil.move(str(backup), str(folder))
    switch.unlink()
    rebuild()
    names = ", ".join(f["name"] for f in faults.armed_faults(fault))
    return f"reset: {names} removed, {fault['date']} restored, warehouse rebuilt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python inject.py",
                                     description="Inject a pipeline fault, or undo it.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--scenario", type=int, choices=sorted(SCENARIOS),
                        help="; ".join(f"{n}: {name}" for n, name in SCENARIOS.items()))
    action.add_argument("--reset", action="store_true", help="undo the armed fault")
    parser.add_argument("--date", type=date.fromisoformat,
                        help="day to break, YYYY-MM-DD (default: latest ingested day)")
    parser.add_argument("--fraction", type=float, default=0.3,
                        help="share of prices blanked by the null spike (default: 0.3)")
    args = parser.parse_args(argv)

    if args.reset:
        print(reset())
        return 0
    name = SCENARIOS[args.scenario]
    day = args.date or latest_day()
    params = {"fraction": args.fraction} if name == "null_spike" else {}
    fault = inject(name, day, params)
    print(f"armed: {name} on {fault['date']} {params or ''}".rstrip())
    run_pipeline(day)  # the incident: a normal daily run that suffers the fault
    return 0


if __name__ == "__main__":
    sys.exit(main())
