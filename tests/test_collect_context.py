import json

from pipeline.collect_context import collect_context

# The `state` fixture lives in conftest.py.


def test_the_context_never_shows_a_traceback(state):
    injected_500 = {"run_id": "r9", "mode": "full", "data_date": "2026-09-13", "step": "ingest",
                    "status": "failed", "error": "HTTPError: HTTP Error 500: Internal Server Error",
                    "traceback": 'File "pipeline/faults.py", line 73, in _server_error',
                    "output": 'File "pipeline/faults.py", line 73, in _server_error'}  # old format
    with state["journal"].open("a", encoding="utf-8") as f:
        f.write(json.dumps(injected_500) + "\n")

    text = json.dumps(collect_context(**state)["last_run"])

    assert "HTTP Error 500" in text
    assert "faults.py" not in text


def test_every_section_is_present_and_readable(state):
    context = collect_context(**state)

    assert set(context) == {"last_run", "dbt_results", "raw_files", "lineage", "warehouse_stats"}
    assert not any("error" in section for section in context.values())


def test_last_run_is_the_most_recent_run(state):
    run = collect_context(**state)["last_run"]

    assert run["run_id"] == "r2"
    assert run["status"] == "failed"
    assert [(s["step"], s["status"]) for s in run["steps"]] == [
        ("ingest", "success"), ("transform", "success"), ("test", "failed")]


def test_dbt_results_come_from_the_latest_steps(state):
    results = collect_context(**state)["dbt_results"]

    assert results["test"]["run_id"] == "r2"
    assert results["test"]["results"][0]["failures"] == 1


def test_lineage_keeps_models_and_sources_only(state):
    assert collect_context(**state)["lineage"] == {
        "stg_prices": ["source:raw.prices"],
        "fct_prices": ["stg_prices"],
    }


def test_raw_files_describe_the_run_day(state):
    raw = collect_context(**state)["raw_files"]

    assert raw["exists"] is True
    assert raw["files"]["prices.csv"] == {
        "columns": ["pdv_id", "prix_id", "prix_nom", "prix_maj", "prix_valeur"], "rows": 1}


def test_stats_cover_the_last_two_days(state):
    stats = collect_context(**state)["warehouse_stats"]

    assert stats["latest_snapshot_date"] == "2026-09-12"
    today = stats["by_day"]["2026-09-12"]
    assert today["prices"] == 2 and today["stations"] == 2
    assert today["null_counts"]["price_eur_per_liter"] == 1
    assert today["avg_price_by_fuel"] == {"Gazole": 2.31}
    assert stats["by_day"]["2026-09-11"]["avg_price_by_fuel"] == {"Gazole": 2.31}


def test_a_broken_pipeline_still_gives_a_context(state, tmp_path):
    state.update(warehouse=tmp_path / "missing.duckdb", manifest=tmp_path / "missing.json",
                 raw_dir=tmp_path / "empty")

    context = collect_context(**state)

    assert "error" in context["warehouse_stats"]
    assert "error" in context["lineage"]
    assert context["raw_files"]["exists"] is False
    assert context["last_run"]["run_id"] == "r2"
