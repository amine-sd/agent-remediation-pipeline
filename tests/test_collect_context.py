import json
from datetime import date

import duckdb

from pipeline.collect_context import day_stats, last_run, lineage, raw_files, read_journal

# The `state` fixture lives in conftest.py.


def test_the_last_run_never_shows_a_traceback(state):
    injected_500 = {"run_id": "r9", "mode": "full", "data_date": "2026-09-13", "step": "ingest",
                    "status": "failed", "error": "HTTPError: HTTP Error 500: Internal Server Error",
                    "traceback": 'File "pipeline/faults.py", line 73, in _server_error',
                    "output": 'File "pipeline/faults.py", line 73, in _server_error'}  # old format
    with state["journal"].open("a", encoding="utf-8") as f:
        f.write(json.dumps(injected_500) + "\n")

    text = json.dumps(last_run(read_journal(state["journal"])))

    assert "HTTP Error 500" in text
    assert "faults.py" not in text


def test_last_run_is_the_most_recent_run(state):
    run = last_run(read_journal(state["journal"]))

    assert run["run_id"] == "r2"
    assert run["status"] == "failed"
    assert [(s["step"], s["status"]) for s in run["steps"]] == [
        ("ingest", "success"), ("transform", "success"), ("test", "failed")]


def test_lineage_keeps_models_and_sources_only(state):
    assert lineage(state["manifest"]) == {
        "stg_prices": ["source:raw.prices"],
        "fct_prices": ["stg_prices"],
    }


def test_raw_files_describe_the_day_or_say_it_is_missing(state, tmp_path):
    raw = raw_files("2026-09-12", state["raw_dir"])

    assert raw["exists"] is True
    assert raw["files"]["prices.csv"] == {
        "columns": ["pdv_id", "prix_id", "prix_nom", "prix_maj", "prix_valeur"], "rows": 1}
    assert raw_files("2026-09-12", tmp_path / "empty")["exists"] is False


def test_day_stats_count_volumes_empty_values_and_average_prices(state):
    con = duckdb.connect(str(state["warehouse"]), read_only=True)
    try:
        today = day_stats(con, date(2026, 9, 12))
    finally:
        con.close()

    assert today["prices"] == 2 and today["stations"] == 2
    assert today["null_counts"]["price_eur_per_liter"] == 1
    assert today["avg_price_by_fuel"] == {"Gazole": 2.31}
