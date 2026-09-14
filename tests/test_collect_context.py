import json

import duckdb
import pytest

from pipeline.collect_context import collect_context

MANIFEST = {"parent_map": {
    "source.fuel_prices.raw.prices": [],
    "model.fuel_prices.stg_prices": ["source.fuel_prices.raw.prices"],
    "model.fuel_prices.fct_prices": ["model.fuel_prices.stg_prices"],
    "test.fuel_prices.not_null_fct_prices_price_eur_per_liter.abc": ["model.fuel_prices.fct_prices"],
}}


def entry(run_id, step, status, **extra):
    return {"run_id": run_id, "mode": "full", "data_date": "2026-09-12", "step": step,
            "status": status, "started_at": "2026-09-13T05:00:00",
            "finished_at": "2026-09-13T05:00:01", "output": "", **extra}


def dbt(*results):
    return {"details": {"dbt_results": list(results)}}


def make_warehouse(path):
    con = duckdb.connect(str(path))
    con.execute("""create table fct_prices (snapshot_date date, station_id varchar, fuel_id integer,
                   fuel_name varchar, price_updated_at timestamp, price_eur_per_liter double)""")
    con.execute("""insert into fct_prices values
        ('2026-09-11', 's1', 1, 'Gazole', '2026-09-11 08:00', 2.30),
        ('2026-09-11', 's2', 1, 'Gazole', '2026-09-11 08:00', 2.32),
        ('2026-09-12', 's1', 1, 'Gazole', '2026-09-12 08:00', 2.31),
        ('2026-09-12', 's2', 1, 'Gazole', '2026-09-12 08:00', null)""")
    con.close()


@pytest.fixture
def state(tmp_path):
    journal = tmp_path / "journal.jsonl"
    failed_test = {"node": "test.fuel_prices.not_null_fct_prices_price_eur_per_liter.abc",
                   "status": "fail", "message": "Got 1 result", "failures": 1}
    entries = [
        entry("r1", "ingest", "success"),
        entry("r1", "transform", "success", **dbt()),
        entry("r1", "test", "success", **dbt()),
        entry("r2", "ingest", "success"),
        entry("r2", "transform", "success", **dbt()),
        entry("r2", "test", "failed", **dbt(failed_test)),
    ]
    journal.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST), encoding="utf-8")

    warehouse = tmp_path / "warehouse.duckdb"
    make_warehouse(warehouse)

    day = tmp_path / "raw" / "2026-09-12"
    day.mkdir(parents=True)
    (day / "prices.csv").write_text(
        "pdv_id,prix_id,prix_nom,prix_maj,prix_valeur\n1,1,Gazole,2026-09-12T08:00:00,2.31\n",
        encoding="utf-8")
    (day / "stations.csv").write_text("pdv_id,cp\n1,01000\n", encoding="utf-8")

    return {"journal": journal, "warehouse": warehouse, "manifest": manifest,
            "raw_dir": tmp_path / "raw"}


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
