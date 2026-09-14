"""The agent's six tools, and the JSON schemas that describe them to the model.

The tools report facts and never conclude: deciding what the facts mean is the agent's job,
and a tool that pre-digested the diagnosis would blur what the benchmark measures.

Every call goes through call_tool(). It only passes the arguments declared in the schema, so
the model cannot redirect a tool to other files, and it turns any exception into an
{"error": ...} answer, so a failing tool never stops the agent.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

import duckdb

from pipeline.collect_context import (MANIFEST, WAREHOUSE, day_stats, last_run, lineage,
                                      raw_files, read_journal)
from pipeline.ingest import RAW_DIR
from pipeline.run import JOURNAL, STEPS, run_pipeline

TICKETS = Path("logs/tickets.jsonl")
CAUSES = ["schema_drift", "null_spike", "duplicate_rows", "freshness", "unit_drift",
          "source_error", "none", "unknown"]
MAX_RUNS = 5
# Small on purpose: the model reads everything in a context of 4096 tokens, shared by the
# system prompt, the schemas and every tool answer of the investigation.
MAX_ROWS = 20
MAX_OUTPUT_CHARS = 600
# read_only alone still lets `COPY ... TO` write a file on disk (checked with DuckDB 1.5.5):
# external access is switched off too, and locked so that a query cannot switch it back on.
LOCKED = {"enable_external_access": False, "lock_configuration": True}


def _connect(warehouse: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(warehouse), read_only=True, config=LOCKED)


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _data_date(data_date: str | None, journal: Path) -> str:
    return data_date or last_run(read_journal(journal))["data_date"]


# 1. Read the logs

def _step_view(entry: dict) -> dict:
    view = {key: entry.get(key) for key in ("step", "status", "started_at", "finished_at")}
    if entry.get("error"):
        view["error"] = entry["error"]
    details = entry.get("details") or {}
    if "dbt_results" in details:
        results = details["dbt_results"]
        view["dbt"] = {"nodes": len(results), "not_passing": [
            {key: r[key] for key in ("node", "status", "failures", "message")}
            for r in results if r["status"] not in ("success", "pass")]}
    elif details:
        view["details"] = details
    # Only the output of a step that failed without raising (a dbt log, typically). A step that
    # raised is described by its error; its traceback stays in the journal, for humans.
    if entry["status"] == "failed" and entry.get("output") and not entry.get("error"):
        view["output_tail"] = entry["output"][-MAX_OUTPUT_CHARS:]
    return view


def read_logs(runs: int = 1, journal: Path = JOURNAL) -> dict:
    """The most recent pipeline runs, newest first, as the journal recorded them."""
    entries = read_journal(journal)
    count = max(1, min(int(runs), MAX_RUNS))
    run_ids = list(dict.fromkeys(e["run_id"] for e in reversed(entries)))[:count]
    views = []
    for run_id in run_ids:
        steps = [e for e in entries if e["run_id"] == run_id]
        views.append({"run_id": run_id, "mode": steps[0]["mode"], "data_date": steps[0]["data_date"],
                      "steps": [_step_view(e) for e in steps]})
    return {"runs": views}


# 2. Query the warehouse

def query_warehouse(sql: str, warehouse: Path = WAREHOUSE) -> dict:
    """Run one read-only query and return at most MAX_ROWS rows."""
    con = _connect(warehouse)
    try:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(MAX_ROWS + 1)
    finally:
        con.close()
    return {"columns": columns,
            "rows": [[_jsonable(v) for v in row] for row in rows[:MAX_ROWS]],
            "truncated": len(rows) > MAX_ROWS}


# 3. Read the lineage

def _walk(start: str, edges: dict[str, list[str]]) -> list[str]:
    seen, stack = set(), list(edges.get(start, []))
    while stack:
        node = stack.pop()
        if node not in seen:
            seen.add(node)
            stack.extend(edges.get(node, []))
    return sorted(seen)


def read_lineage(model: str | None = None, manifest: Path = MANIFEST) -> dict:
    """Which models and sources each model reads from; for one model, all it depends on and feeds."""
    parents = lineage(manifest)
    if model is None:
        return {"lineage": parents}
    children: dict[str, list[str]] = {}
    for child, its_parents in parents.items():
        for parent in its_parents:
            children.setdefault(parent, []).append(child)
    if model not in parents and model not in children:
        return {"error": f"unknown model {model!r}; known: {sorted(set(parents) | set(children))}"}
    return {"model": model, "upstream": _walk(model, parents), "downstream": _walk(model, children)}


# 4. Compare with the previous run

def _ratio(value, reference):
    return None if value is None or not reference else round(value / reference, 4)


def _column_changes(day: dict, previous: dict) -> dict:
    changes = {}
    for name in ("stations.csv", "prices.csv"):
        now = ((day.get("files") or {}).get(name) or {}).get("columns")
        before = ((previous.get("files") or {}).get(name) or {}).get("columns")
        if now is not None and before is not None and now != before:
            changes[name] = {"added": [c for c in now if c not in before],
                             "removed": [c for c in before if c not in now]}
    return changes


def compare_with_previous_run(data_date: str | None = None, journal: Path = JOURNAL,
                              warehouse: Path = WAREHOUSE, raw_dir: Path = RAW_DIR) -> dict:
    """One day against the previous day present in the warehouse: facts and ratios, no verdict."""
    day = _data_date(data_date, journal)
    con = _connect(warehouse)
    try:
        (previous,) = con.execute("select max(snapshot_date) from fct_prices where snapshot_date < ?",
                                  [date.fromisoformat(day)]).fetchone()
        now = day_stats(con, date.fromisoformat(day))
        before = day_stats(con, previous) if previous else None
    finally:
        con.close()
    previous_day = previous.isoformat() if previous else None
    raw_now = raw_files(day, raw_dir)
    raw_before = raw_files(previous_day, raw_dir) if previous_day else {}
    changes = None
    if before:
        changes = {
            "prices_ratio": _ratio(now["prices"], before["prices"]),
            "avg_price_ratio_by_fuel": {fuel: _ratio(now["avg_price_by_fuel"].get(fuel), avg)
                                        for fuel, avg in before["avg_price_by_fuel"].items()},
        }
    return {"day": day, "previous_day": previous_day,
            "warehouse": {"day": now, "previous": before, "changes": changes},
            "raw_files": {"day": raw_now, "previous": raw_before or None,
                          "column_changes": _column_changes(raw_now, raw_before)}}


# 5. Rerun a step

def rerun_step(step: str, data_date: str | None = None, journal: Path = JOURNAL,
               runner: Callable[..., bool] = run_pipeline) -> dict:
    """Run one step again and report how it ended. Which reruns are allowed is not decided here
    but by the guardrail around the tools, as the autonomy policy requires."""
    if step not in STEPS:
        return {"error": f"unknown step {step!r}; steps: {list(STEPS)}"}
    day = _data_date(data_date, journal)
    runner(date.fromisoformat(day), steps=(step,), journal=journal)
    entry = read_journal(journal)[-1]
    result = {"run_id": entry["run_id"], "step": step, "data_date": day, "status": entry["status"]}
    if entry.get("error"):
        result["error"] = entry["error"]
    return result


# 6. Open a ticket

def open_ticket(causes: list[str], justification: str, proposed_action: str,
                tickets: Path = TICKETS) -> dict:
    """Escalate to a human: the diagnosis is recorded in logs/tickets.jsonl."""
    unknown = [c for c in causes if c not in CAUSES]
    if unknown:
        return {"error": f"unknown causes {unknown}; allowed: {CAUSES}"}
    ticket = {"ticket_id": f"T-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}",
              "opened_at": datetime.now().isoformat(timespec="seconds"),
              "causes": causes, "justification": justification, "proposed_action": proposed_action}
    tickets.parent.mkdir(parents=True, exist_ok=True)
    with tickets.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ticket, ensure_ascii=False) + "\n")
    return {"ticket_id": ticket["ticket_id"]}


# Schemas, in the format Ollama expects for tool calling

def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


DAY_ARGUMENT = {"type": "string", "description": "Optional day, YYYY-MM-DD. Default: the data date of the last run."}

SCHEMAS = [
    _schema("read_logs",
            "Read the pipeline journal: the most recent runs, newest first, with each step's "
            "status, error, the dbt models and tests that did not pass, and the output of failed steps.",
            {"runs": {"type": "integer", "minimum": 1, "maximum": MAX_RUNS,
                      "description": "How many recent runs to return. Default 1."}},
            []),
    _schema("query_warehouse",
            f"Run one read-only SQL query (DuckDB) on the warehouse, at most {MAX_ROWS} rows back. Tables: "
            "fct_prices (snapshot_date, station_id, fuel_id, fuel_name, price_updated_at, "
            "price_eur_per_liter); dim_stations (station_id, latitude, longitude, postal_code, "
            "department_code, location_type, address, city, has_24h_automat, services, "
            "last_seen_date); agg_prices_by_department (snapshot_date, department_code, fuel_name, "
            "n_stations, avg_price, min_price, max_price). Raw files cannot be read from SQL.",
            {"sql": {"type": "string", "description": "One SQL query."}},
            ["sql"]),
    _schema("read_lineage",
            "Read the dbt lineage: which models and sources each model reads from. Give a model "
            "name to get everything upstream and downstream of it.",
            {"model": {"type": "string", "description": "Optional model name, for example fct_prices."}},
            []),
    # No date argument for the model: given one, a 3B model compared the wrong day (J10). The
    # function keeps the parameter for tests and the benchmark; call_tool never passes it.
    _schema("compare_with_previous_run",
            "Compare the day of the last run (the data date in the alert) with the previous day "
            "in the warehouse: row counts, empty values, average price per fuel with ratios, and "
            "the raw files' columns and row counts.",
            {},
            []),
    _schema("rerun_step",
            "Run one pipeline step again and get how it ended.",
            {"step": {"type": "string", "enum": list(STEPS)}, "data_date": DAY_ARGUMENT},
            ["step"]),
    _schema("open_ticket",
            "Escalate to a human by opening a ticket with your diagnosis.",
            {"causes": {"type": "array", "items": {"type": "string", "enum": CAUSES}},
             "justification": {"type": "string", "description": "What you observed and why it leads to these causes."},
             "proposed_action": {"type": "string", "description": "What you would do, or advise a human to do."}},
            ["causes", "justification", "proposed_action"]),
]

FUNCTIONS: dict[str, Callable[..., dict]] = {
    "read_logs": read_logs,
    "query_warehouse": query_warehouse,
    "read_lineage": read_lineage,
    "compare_with_previous_run": compare_with_previous_run,
    "rerun_step": rerun_step,
    "open_ticket": open_ticket,
}


def call_tool(name: str, arguments: dict) -> dict:
    schema = next((s["function"] for s in SCHEMAS if s["function"]["name"] == name), None)
    if schema is None:
        return {"error": f"unknown tool {name!r}; tools: {list(FUNCTIONS)}"}
    declared = schema["parameters"]["properties"]
    unexpected = sorted(set(arguments) - set(declared))
    missing = [r for r in schema["parameters"]["required"] if r not in arguments]
    if unexpected or missing:
        return {"error": f"bad arguments for {name}: unexpected {unexpected}, missing {missing}"}
    try:
        return FUNCTIONS[name](**arguments)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
