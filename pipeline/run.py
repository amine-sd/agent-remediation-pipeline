"""Chain the pipeline steps (ingest, transform, test) and journal each one.

Every step appends one JSON line to logs/journal.jsonl: when it ran, how it ended, and
what it printed. That journal is what the agent will read, and `--step` is what its
"rerun a step" tool will call.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from pipeline import faults
from pipeline.ingest import ingest_day

JOURNAL = Path("logs/journal.jsonl")
DBT_PROJECT = Path("transform")
STEPS = ("ingest", "transform", "test")
OUTPUT_TAIL_LINES = 40


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _tail(text: str, lines: int = OUTPUT_TAIL_LINES) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _dbt_executable() -> str:
    # The dbt installed next to the running interpreter, so the venv needs no activation.
    return str(Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt"))


def run_ingest(day: date) -> dict:
    # An armed fault (pipeline/faults.py) changes where the archive comes from and what gets
    # written. Nothing in the journal says so, as in a real incident.
    fault = faults.armed()
    summary = ingest_day(day, fetch=faults.fetch_for(day, fault),
                         tamper=faults.tamper_for(day, fault))
    return {"ok": True, "output": json.dumps(summary), "details": summary}


def run_dbt(command: str, project: Path = DBT_PROJECT) -> dict:
    """Run one dbt command; keep the tail of what it printed and its per-node results."""
    run_results = project / "target" / "run_results.json"
    # A dbt command that crashes early writes no run_results.json: without this, the journal
    # would silently report the previous command's results.
    run_results.unlink(missing_ok=True)
    completed = subprocess.run(
        [_dbt_executable(), command, "--project-dir", str(project), "--profiles-dir", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    results = []
    if run_results.exists():
        results = [
            {"node": r["unique_id"], "status": r["status"], "message": r.get("message"),
             "failures": r.get("failures")}
            for r in json.loads(run_results.read_text(encoding="utf-8"))["results"]
        ]
    return {"ok": completed.returncode == 0,
            "output": _tail(completed.stdout + completed.stderr),
            "details": {"dbt_results": results}}


def _append(journal: Path, entry: dict) -> None:
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_pipeline(day: date, steps: tuple[str, ...] = STEPS, journal: Path = JOURNAL,
                 step_functions: dict[str, Callable[[], dict]] | None = None) -> bool:
    """Run the steps in order. After a failure, the following steps are journaled as skipped."""
    step_functions = step_functions or {
        "ingest": lambda: run_ingest(day),
        "transform": lambda: run_dbt("run"),
        "test": lambda: run_dbt("test"),
    }
    # The random suffix keeps two runs started in the same second apart.
    run_id = f"{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    mode = "full" if tuple(steps) == STEPS else "single-step"
    failed = False
    for step in steps:
        entry = {"run_id": run_id, "mode": mode, "data_date": day.isoformat(), "step": step,
                 "started_at": _now()}
        if failed:
            entry.update(status="skipped", output="previous step failed")
        else:
            try:
                result = step_functions[step]()
                entry.update(status="success" if result["ok"] else "failed",
                             output=result["output"], details=result.get("details"))
            except Exception as exc:
                # The traceback is kept for humans, apart from the output: the agent never reads
                # it, since for an injected fault it would name the injector's own code.
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                             traceback=_tail(traceback.format_exc()))
            failed = entry["status"] == "failed"
        entry["finished_at"] = _now()
        _append(journal, entry)
        print(f"[{entry['status']}] {step}")
    return not failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline",
                                     description="Run the pipeline and journal each step.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run every step, or a single one with --step")
    run.add_argument("--date", type=date.fromisoformat, default=date.today() - timedelta(days=1),
                     help="data date to ingest, YYYY-MM-DD (default: yesterday)")
    run.add_argument("--step", choices=STEPS, help="run only this step")
    args = parser.parse_args(argv)
    steps = (args.step,) if args.step else STEPS
    return 0 if run_pipeline(args.date, steps) else 1
