"""A small pipeline state, shared by the tests of the context collection and of the agent's tools:
two runs in the journal (the second one with a failed test), a manifest, a warehouse with two
snapshot days, and the raw files of the last day."""

import json

import duckdb
import pytest

MANIFEST = {"parent_map": {
    "source.fuel_prices.raw.prices": [],
    "model.fuel_prices.stg_prices": ["source.fuel_prices.raw.prices"],
    "model.fuel_prices.fct_prices": ["model.fuel_prices.stg_prices"],
    "test.fuel_prices.not_null_fct_prices_price_eur_per_liter.abc": ["model.fuel_prices.fct_prices"],
}}

FAILED_TEST = {"node": "test.fuel_prices.not_null_fct_prices_price_eur_per_liter.abc",
               "status": "fail", "message": "Got 1 result", "failures": 1}


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
    entries = [
        entry("r1", "ingest", "success"),
        entry("r1", "transform", "success", **dbt()),
        entry("r1", "test", "success", **dbt()),
        entry("r2", "ingest", "success"),
        entry("r2", "transform", "success", **dbt()),
        entry("r2", "test", "failed", output="Failure in test not_null_fct_prices_price_eur_per_liter",
              **dbt(FAILED_TEST)),
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
