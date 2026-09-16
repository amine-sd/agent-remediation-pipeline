"""The baseline without a model: fixed rules that read what the agent reads (the journal, the dbt
tests, the comparison with the previous day) and apply the same decision table.

The rules were written from the definition of the faults (pipeline/faults.py) and then frozen:
they are never tuned on benchmark results (docs/mesures.md). Honest caveat: they were written
after the agent's first benchmark results were known, not before as the measurement sheet asks.
"""

from __future__ import annotations

import json

from agent.tools import compare_with_previous_run, read_logs
from bench.runner import CAUTION, policy_decision

UNIT_RATIO = (0.5, 2.0)  # an average price that halves or doubles overnight changed unit or scale
VOLUME_DROP = 0.8  # under 80 % of yesterday's prices with nothing else to explain it: unknown fault


def diagnose(view: dict, comparison: dict) -> tuple[list[str], dict]:
    """The causes the rules find in one run, and the facts behind them."""
    ingest = next((s for s in view["steps"] if s["step"] == "ingest"), {})
    error = ingest.get("error") or ""
    if error.startswith("HTTPError"):
        return ["source_error"], {"error": error}
    if error.startswith("SourceUnavailable"):
        return ["freshness"], {"error": error}

    causes, facts = [], {}
    columns = comparison["raw_files"].get("column_changes") or {}
    if columns:
        causes.append("schema_drift")
        facts["column_changes"] = columns
    failing = [n["node"].split(".")[2] for s in view["steps"]
               for n in (s.get("dbt") or {}).get("not_passing", []) if n["node"].count(".") >= 2]
    if any(name.startswith("unique_") for name in failing):
        causes.append("duplicate_rows")
        facts["failing_tests"] = failing
    day = comparison["warehouse"]["day"]
    nulls = day["null_counts"].get("price_eur_per_liter", 0)
    if nulls and "schema_drift" not in causes:  # a renamed column also empties every price
        causes.append("null_spike")
        facts["null_fraction"] = round(nulls / day["prices"], 4) if day["prices"] else 1.0
    changes = comparison["warehouse"].get("changes") or {}
    ratios = [r for r in (changes.get("avg_price_ratio_by_fuel") or {}).values() if r is not None]
    if any(r < UNIT_RATIO[0] or r > UNIT_RATIO[1] for r in ratios):
        causes.append("unit_drift")
        facts["price_ratios"] = ratios
    volume = changes.get("prices_ratio")
    if not causes and volume is not None and volume < VOLUME_DROP:
        causes.append("unknown")
        facts["prices_ratio"] = volume
    return causes or ["none"], facts


def decide(causes: list[str], facts: dict, view: dict) -> str:
    """The decision table of the autonomy policy, the most cautious decision winning."""
    already_rerun = view.get("triggered_by") == "agent"
    fault_of = {"none": "healthy", "unknown": "truncated_delivery"}
    decisions = [policy_decision(fault_of.get(c, c), already_rerun,
                                 {"fraction": facts["null_fraction"]} if c == "null_spike" else None)
                 for c in causes]
    return max(decisions, key=CAUTION.index)


def rules_agent(alert_text: str) -> dict:
    """Stands where the agent stands in the benchmark, and answers in the same format."""
    view = read_logs(runs=1)["runs"][0]
    causes, facts = diagnose(view, compare_with_previous_run())
    verdict = {"causes": causes, "decision": decide(causes, facts, view),
               "justification": "rules: " + json.dumps(facts, ensure_ascii=False),
               "proposed_action": "baseline rules"}
    return {"verdict": verdict, "stopped": "answered", "error": None, "raw_verdict": None,
            "notes": None, "tool_calls": [{"tool": "read_logs"}, {"tool": "compare_with_previous_run"}],
            "usage": {"model_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "model_seconds": 0.0},
            "prompt_chars": 0, "messages": []}
