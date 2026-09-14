import json

import pytest

from agent.guardrail import execute, guarded_rerun

DAY = "2026-09-13"


@pytest.fixture
def paths(tmp_path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text("", encoding="utf-8")
    return {"journal": journal, "decisions": tmp_path / "decisions.jsonl",
            "tickets": tmp_path / "tickets.jsonl"}


def agent_run(decision, causes=("source_error",)):
    return {"stopped": "answered", "error": None,
            "verdict": {"causes": list(causes), "justification": "HTTP 500 at the source.",
                        "decision": decision, "proposed_action": "Rerun the ingestion."}}


def fake_rerun(calls):
    """Stands in for rerun_step: records the call and journals an agent rerun, as the real one does."""
    def rerun(step, data_date, journal):
        calls.append((step, data_date))
        run_id = f"rerun-{len(calls)}"
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": run_id, "triggered_by": "agent", "data_date": data_date,
                                "step": step, "status": "failed"}) + "\n")
        return {"run_id": run_id, "step": step, "data_date": data_date, "status": "failed"}
    return rerun


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_first_rerun_of_the_ingestion_is_executed(paths):
    calls = []

    record = execute(agent_run("rerun_ingestion"), DAY, "run-0", rerun=fake_rerun(calls), **paths)

    assert record["outcome"] == "executed" and record["rerun"]["status"] == "failed"
    assert calls == [("ingest", DAY)]
    assert not paths["tickets"].exists()


def test_a_second_rerun_for_the_same_day_is_refused_traced_and_escalated(paths):
    calls = []
    rerun = fake_rerun(calls)
    execute(agent_run("rerun_ingestion"), DAY, "run-0", rerun=rerun, **paths)

    record = execute(agent_run("rerun_ingestion"), DAY, "rerun-1", rerun=rerun, **paths)

    assert record["outcome"] == "refused" and "already rerun once" in record["reason"]
    assert calls == [("ingest", DAY)]  # never executed a second time
    (ticket,) = lines(paths["tickets"])
    assert ticket["ticket_id"] == record["ticket"] and "Refused by the guardrail" in ticket["justification"]
    assert [d["outcome"] for d in lines(paths["decisions"])] == ["executed", "refused"]


def test_rerunning_a_step_off_the_whitelist_is_refused_and_never_run(paths):
    calls = []

    attempt = guarded_rerun("transform", DAY, paths["journal"], rerun=fake_rerun(calls))

    assert attempt["allowed"] is False and "not on the whitelist" in attempt["reason"]
    assert calls == []


def test_escalate_opens_a_ticket_and_close_changes_nothing(paths):
    calls = []

    escalated = execute(agent_run("escalate", ["schema_drift"]), DAY, "run-0", rerun=fake_rerun(calls), **paths)
    closed = execute(agent_run("close", ["none"]), DAY, "run-0", rerun=fake_rerun(calls), **paths)

    assert escalated["outcome"] == "escalated" and escalated["ticket"]
    assert closed["outcome"] == "closed" and "ticket" not in closed
    assert calls == [] and len(lines(paths["tickets"])) == 1


def test_an_unknown_cause_can_never_act_alone(paths):
    calls = []

    record = execute(agent_run("rerun_ingestion", ["unknown"]), DAY, "run-0", rerun=fake_rerun(calls), **paths)

    assert record["outcome"] == "refused" and record["ticket"]
    assert calls == []


@pytest.mark.parametrize("stopped", ["budget", "invalid_output", "model_error"])
def test_no_verdict_means_an_imposed_escalation(paths, stopped):
    record = execute({"stopped": stopped, "error": "boom", "verdict": None}, DAY, "run-0",
                     rerun=fake_rerun([]), **paths)

    assert record["outcome"] == "imposed_escalation" and record["ticket"]
    assert lines(paths["decisions"])[0]["stopped"] == stopped


def test_every_decision_is_timestamped_and_justified(paths):
    execute(agent_run("escalate", ["schema_drift"]), DAY, "run-0", rerun=fake_rerun([]), **paths)

    (record,) = lines(paths["decisions"])
    assert record["decided_at"]
    assert record["justification"] == "HTTP 500 at the source."
    assert (record["data_date"], record["incident_run_id"], record["decision"]) == (DAY, "run-0", "escalate")
