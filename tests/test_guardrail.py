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


def incident(journal, run_id="run-0", ingest="success", test="success"):
    """Journal the run the agent investigates, as the pipeline would: after a failed step, the
    following ones are skipped."""
    statuses = {"ingest": ingest, "transform": "success", "test": test}
    failed = False
    with journal.open("a", encoding="utf-8") as f:
        for step, status in statuses.items():
            status = "skipped" if failed else status
            failed = failed or status == "failed"
            f.write(json.dumps({"run_id": run_id, "triggered_by": "cli", "data_date": DAY,
                                "step": step, "status": status}) + "\n")


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


def test_a_first_rerun_of_a_failed_ingestion_is_executed(paths):
    incident(paths["journal"], ingest="failed")
    calls = []

    record = execute(agent_run("rerun_ingestion"), DAY, "run-0", rerun=fake_rerun(calls), **paths)

    assert record["outcome"] == "executed" and record["rerun"]["status"] == "failed"
    assert calls == [("ingest", DAY)]
    assert not paths["tickets"].exists()


def test_a_second_rerun_for_the_same_day_is_refused_traced_and_escalated(paths):
    incident(paths["journal"], ingest="failed")
    calls = []
    rerun = fake_rerun(calls)
    execute(agent_run("rerun_ingestion"), DAY, "run-0", rerun=rerun, **paths)

    record = execute(agent_run("rerun_ingestion"), DAY, "rerun-1", rerun=rerun, **paths)

    assert record["outcome"] == "refused" and "already rerun once" in record["reason"]
    assert calls == [("ingest", DAY)]  # never executed a second time
    (ticket,) = lines(paths["tickets"])
    assert ticket["ticket_id"] == record["ticket"] and "Refused by the guardrail" in ticket["justification"]
    assert [d["outcome"] for d in lines(paths["decisions"])] == ["executed", "refused"]


def test_a_rerun_is_refused_when_the_ingestion_did_not_fail_whatever_the_agent_says(paths):
    # The agent blames the source, but the journal shows an ingestion that went through and a test
    # that failed afterwards: a rerun would download the same data again.
    incident(paths["journal"], ingest="success", test="failed")
    calls = []

    record = execute(agent_run("rerun_ingestion", ["source_error"]), DAY, "run-0",
                     rerun=fake_rerun(calls), **paths)

    assert record["outcome"] == "refused" and "did not fail" in record["reason"]
    assert calls == [] and lines(paths["tickets"])[0]["ticket_id"] == record["ticket"]


def test_closing_is_refused_when_a_step_failed(paths):
    incident(paths["journal"], test="failed")

    record = execute(agent_run("close", ["none"]), DAY, "run-0", rerun=fake_rerun([]), **paths)

    assert record["outcome"] == "refused" and "the test step" in record["reason"]
    assert lines(paths["tickets"])[0]["ticket_id"] == record["ticket"]


def test_a_run_the_journal_does_not_know_is_never_acted_on(paths):
    calls = []

    rerun = execute(agent_run("rerun_ingestion"), DAY, "run-9", rerun=fake_rerun(calls), **paths)
    close = execute(agent_run("close", ["none"]), DAY, "run-9", rerun=fake_rerun(calls), **paths)

    assert (rerun["outcome"], close["outcome"]) == ("refused", "refused")
    assert "holds no run 'run-9'" in rerun["reason"] and "holds no run 'run-9'" in close["reason"]
    assert calls == []


def test_rerunning_a_step_off_the_whitelist_is_refused_and_never_run(paths):
    incident(paths["journal"], ingest="failed")
    calls = []

    attempt = guarded_rerun("transform", DAY, "run-0", paths["journal"], rerun=fake_rerun(calls))

    assert attempt["allowed"] is False and "not on the whitelist" in attempt["reason"]
    assert calls == []


def test_escalate_opens_a_ticket_and_a_clean_run_can_be_closed(paths):
    incident(paths["journal"])
    calls = []

    escalated = execute(agent_run("escalate", ["schema_drift"]), DAY, "run-0", rerun=fake_rerun(calls), **paths)
    closed = execute(agent_run("close", ["none"]), DAY, "run-0", rerun=fake_rerun(calls), **paths)

    assert escalated["outcome"] == "escalated" and escalated["ticket"]
    assert closed["outcome"] == "closed" and "ticket" not in closed
    assert calls == [] and len(lines(paths["tickets"])) == 1


def test_an_unknown_cause_can_never_act_alone(paths):
    incident(paths["journal"], ingest="failed")
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
