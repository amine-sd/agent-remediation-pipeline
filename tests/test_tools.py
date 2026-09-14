import json

import pytest

from agent import tools
from agent.tools import (CAUSES, FUNCTIONS, SCHEMAS, call_tool, compare_with_previous_run,
                         open_ticket, query_warehouse, read_lineage, read_logs, rerun_step)

# The `state` fixture lives in conftest.py.


# 1. read_logs

def test_read_logs_gives_the_latest_runs_newest_first(state):
    runs = read_logs(runs=2, journal=state["journal"])["runs"]

    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    test_step = runs[0]["steps"][2]
    assert test_step["status"] == "failed"
    assert test_step["dbt"]["not_passing"][0]["failures"] == 1
    assert "Failure in test" in test_step["output_tail"]
    assert "output_tail" not in runs[0]["steps"][0]  # output only for failed steps


def test_read_logs_never_shows_a_traceback(tmp_path):
    journal = tmp_path / "journal.jsonl"
    injected_500 = {"run_id": "r9", "mode": "full", "data_date": "2026-09-13", "step": "ingest",
                    "status": "failed", "error": "HTTPError: HTTP Error 500: Internal Server Error",
                    "traceback": 'File "pipeline/faults.py", line 73, in _server_error',
                    "output": 'File "pipeline/faults.py", line 73, in _server_error'}  # old format
    journal.write_text(json.dumps(injected_500) + "\n", encoding="utf-8")

    text = json.dumps(read_logs(journal=journal))

    assert "HTTP Error 500" in text
    assert "faults.py" not in text


# 2. query_warehouse

def test_query_warehouse_returns_columns_and_rows(state):
    result = query_warehouse("select snapshot_date, count(*) as n from fct_prices group by 1 order by 1",
                             warehouse=state["warehouse"])

    assert result == {"columns": ["snapshot_date", "n"],
                      "rows": [["2026-09-11", 2], ["2026-09-12", 2]], "truncated": False}


def test_query_warehouse_caps_the_rows(state):
    result = query_warehouse("select * from range(80)", warehouse=state["warehouse"])

    assert len(result["rows"]) == tools.MAX_ROWS and result["truncated"] is True


@pytest.mark.parametrize("sql", [
    "delete from fct_prices",
    "copy (select 1) to 'escaped.csv'",
    "set enable_external_access = true",
])
def test_query_warehouse_cannot_write_anything(state, sql, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(Exception):
        query_warehouse(sql, warehouse=state["warehouse"])
    assert not (tmp_path / "escaped.csv").exists()


# 3. read_lineage

def test_read_lineage_gives_the_whole_map(state):
    assert read_lineage(manifest=state["manifest"])["lineage"] == {
        "stg_prices": ["source:raw.prices"], "fct_prices": ["stg_prices"]}


def test_read_lineage_walks_up_and_down_from_a_model(state):
    assert read_lineage("stg_prices", manifest=state["manifest"]) == {
        "model": "stg_prices", "upstream": ["source:raw.prices"], "downstream": ["fct_prices"]}
    assert "error" in read_lineage("no_such_model", manifest=state["manifest"])


# 4. compare_with_previous_run

def test_compare_puts_the_run_day_next_to_the_previous_one(state):
    previous = state["raw_dir"] / "2026-09-11"
    previous.mkdir()
    (previous / "prices.csv").write_text("pdv_id,prix_id,prix_nom,prix_maj,valeur\n", encoding="utf-8")

    result = compare_with_previous_run(journal=state["journal"], warehouse=state["warehouse"],
                                       raw_dir=state["raw_dir"])

    assert (result["day"], result["previous_day"]) == ("2026-09-12", "2026-09-11")
    assert result["warehouse"]["day"]["null_counts"]["price_eur_per_liter"] == 1
    assert result["warehouse"]["changes"] == {"prices_ratio": 1.0,
                                              "avg_price_ratio_by_fuel": {"Gazole": 1.0}}
    assert result["raw_files"]["column_changes"] == {
        "prices.csv": {"added": ["prix_valeur"], "removed": ["valeur"]}}


def test_compare_on_a_day_that_never_arrived(state):
    result = compare_with_previous_run("2026-09-13", journal=state["journal"],
                                       warehouse=state["warehouse"], raw_dir=state["raw_dir"])

    assert result["previous_day"] == "2026-09-12"
    assert result["warehouse"]["day"]["prices"] == 0
    assert result["raw_files"]["day"]["exists"] is False


# 5. rerun_step

def test_rerun_step_runs_the_step_and_reports_how_it_ended(state):
    calls = []

    def runner(day, steps, journal):
        calls.append((day.isoformat(), steps))
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": "r3", "step": "ingest", "status": "failed",
                                "error": "HTTPError: HTTP Error 500: Internal Server Error"}) + "\n")
        return False

    result = rerun_step("ingest", journal=state["journal"], runner=runner)

    assert calls == [("2026-09-12", ("ingest",))]
    assert result == {"run_id": "r3", "step": "ingest", "data_date": "2026-09-12",
                      "status": "failed", "error": "HTTPError: HTTP Error 500: Internal Server Error"}
    assert "error" in rerun_step("deploy", journal=state["journal"], runner=runner)


# 6. open_ticket

def test_open_ticket_records_the_diagnosis(tmp_path):
    tickets = tmp_path / "tickets.jsonl"

    result = open_ticket(["unit_drift"], "Average price x1000 since yesterday.",
                         "Ask the source about the unit.", tickets=tickets)

    (recorded,) = [json.loads(line) for line in tickets.read_text(encoding="utf-8").splitlines()]
    assert recorded["ticket_id"] == result["ticket_id"]
    assert recorded["causes"] == ["unit_drift"]


def test_open_ticket_refuses_causes_outside_the_list(tmp_path):
    tickets = tmp_path / "tickets.jsonl"

    assert "error" in open_ticket(["gremlins"], "?", "?", tickets=tickets)
    assert not tickets.exists()


# The dispatcher and the schemas

def test_every_tool_has_a_schema_and_the_other_way_round():
    assert [s["function"]["name"] for s in SCHEMAS] == list(FUNCTIONS)
    ticket_causes = SCHEMAS[-1]["function"]["parameters"]["properties"]["causes"]["items"]["enum"]
    assert ticket_causes == CAUSES


def test_call_tool_refuses_what_the_schema_does_not_declare():
    assert "unknown tool" in call_tool("drop_everything", {})["error"]
    assert "unexpected ['warehouse']" in call_tool("query_warehouse", {"sql": "select 1",
                                                                        "warehouse": "other.duckdb"})["error"]
    assert "missing ['sql']" in call_tool("query_warehouse", {})["error"]


def test_call_tool_turns_a_failure_into_an_answer(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("warehouse locked")

    monkeypatch.setitem(tools.FUNCTIONS, "read_lineage", broken)

    assert call_tool("read_lineage", {}) == {"error": "RuntimeError: warehouse locked"}
