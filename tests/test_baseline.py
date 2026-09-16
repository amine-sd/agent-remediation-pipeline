"""The baseline rules, on the facts each fault leaves behind."""

import pytest

from bench.baseline import decide, diagnose

NORMAL_DAY = {"prices": 31159, "stations": 9487,
              "null_counts": {"price_eur_per_liter": 0}, "avg_price_by_fuel": {"Gazole": 2.31}}


def view(error=None, failing=(), triggered_by="cli"):
    steps = [{"step": "ingest", "status": "failed" if error else "success", "error": error},
             {"step": "test", "status": "failed" if failing else "success",
              "dbt": {"not_passing": [{"node": f"test.fuel_prices.{name}.abc"} for name in failing]}}]
    return {"triggered_by": triggered_by, "steps": steps}


def comparison(nulls=0, prices=31159, prices_ratio=0.98, gazole_ratio=1.0005, columns=None):
    day = {**NORMAL_DAY, "prices": prices, "null_counts": {"price_eur_per_liter": nulls}}
    return {"warehouse": {"day": day, "changes": {"prices_ratio": prices_ratio,
                                                   "avg_price_ratio_by_fuel": {"Gazole": gazole_ratio}}},
            "raw_files": {"column_changes": columns or {}}}


def verdict(v, c):
    causes, facts = diagnose(v, c)
    return causes, decide(causes, facts, v)


@pytest.mark.parametrize("v, c, expected", [
    (view(error="HTTPError: HTTP Error 500: Internal Server Error"), comparison(prices=0),
     (["source_error"], "rerun_ingestion")),
    (view(error="SourceUnavailable: no archive for 2026-09-13"), comparison(prices=0),
     (["freshness"], "rerun_ingestion")),
    (view(failing=["not_null_fct_prices_price_eur_per_liter"]),
     comparison(nulls=31159, gazole_ratio=None, columns={"prices.csv": {"added": ["valeur"]}}),
     (["schema_drift"], "escalate")),
    (view(failing=["unique_fct_prices_price_key"]), comparison(prices=62318, prices_ratio=1.96),
     (["duplicate_rows"], "escalate")),
    (view(failing=["not_null_fct_prices_price_eur_per_liter"]), comparison(nulls=9348),
     (["null_spike"], "escalate")),
    (view(failing=["not_null_fct_prices_price_eur_per_liter"]), comparison(nulls=93),
     (["null_spike"], "close")),
    (view(), comparison(gazole_ratio=1000.49), (["unit_drift"], "escalate")),
    (view(failing=["unique_fct_prices_price_key"]),
     comparison(prices=62318, prices_ratio=1.96, gazole_ratio=1000.49),
     (["duplicate_rows", "unit_drift"], "escalate")),
    (view(), comparison(prices=15677, prices_ratio=0.49), (["unknown"], "escalate")),
    (view(), comparison(), (["none"], "close")),
])
def test_each_fault_leaves_the_facts_the_rules_look_for(v, c, expected):
    assert verdict(v, c) == expected


def test_a_second_failure_after_the_agents_rerun_is_escalated():
    v = view(error="HTTPError: HTTP Error 500: Internal Server Error", triggered_by="agent")

    assert verdict(v, comparison(prices=0)) == (["source_error"], "escalate")
