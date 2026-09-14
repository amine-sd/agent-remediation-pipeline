import json
import subprocess
from datetime import date

from pipeline import run
from pipeline.ingest import SourceUnavailable
from pipeline.run import run_dbt, run_pipeline

DAY = date(2026, 9, 12)


def ok(output="done"):
    return lambda: {"ok": True, "output": output, "details": {}}


def read_journal(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_step_is_journaled_in_order(tmp_path):
    journal = tmp_path / "journal.jsonl"
    steps = {"ingest": ok(), "transform": ok(), "test": ok()}

    assert run_pipeline(DAY, journal=journal, step_functions=steps) is True

    entries = read_journal(journal)
    assert [(e["step"], e["status"]) for e in entries] == [
        ("ingest", "success"), ("transform", "success"), ("test", "success")]
    assert len({e["run_id"] for e in entries}) == 1
    assert all(e["mode"] == "full" and e["data_date"] == "2026-09-12" for e in entries)
    assert all(e["started_at"] and e["finished_at"] for e in entries)


def test_a_failure_skips_the_following_steps(tmp_path):
    journal = tmp_path / "journal.jsonl"

    def missing_archive():
        raise SourceUnavailable("no archive for 2026-09-14")

    steps = {"ingest": missing_archive, "transform": ok(), "test": ok()}

    assert run_pipeline(DAY, journal=journal, step_functions=steps) is False

    entries = read_journal(journal)
    assert [(e["step"], e["status"]) for e in entries] == [
        ("ingest", "failed"), ("transform", "skipped"), ("test", "skipped")]
    assert entries[0]["error"] == "SourceUnavailable: no archive for 2026-09-14"


def test_a_dbt_failure_is_a_failed_step(tmp_path):
    journal = tmp_path / "journal.jsonl"
    steps = {"ingest": ok(),
             "transform": lambda: {"ok": False, "output": "Binder Error", "details": {}},
             "test": ok()}

    assert run_pipeline(DAY, journal=journal, step_functions=steps) is False
    assert [e["status"] for e in read_journal(journal)] == ["success", "failed", "skipped"]


def test_a_single_step_can_be_rerun_alone(tmp_path):
    journal = tmp_path / "journal.jsonl"

    run_pipeline(DAY, steps=("transform",), journal=journal,
                 step_functions={"transform": ok()})

    (entry,) = read_journal(journal)
    assert entry["step"] == "transform" and entry["mode"] == "single-step"


def test_a_traceback_is_kept_apart_from_the_output(tmp_path):
    journal = tmp_path / "journal.jsonl"

    def boom():
        raise RuntimeError("boom")

    run_pipeline(DAY, steps=("ingest",), journal=journal, step_functions={"ingest": boom})

    (entry,) = read_journal(journal)
    assert entry["error"] == "RuntimeError: boom"
    assert "Traceback" in entry["traceback"]
    assert "output" not in entry


def test_the_journal_says_who_asked_for_the_run(tmp_path):
    journal = tmp_path / "journal.jsonl"
    run_pipeline(DAY, steps=("ingest",), journal=journal, step_functions={"ingest": ok()})
    run_pipeline(DAY, steps=("ingest",), journal=journal, step_functions={"ingest": ok()},
                 triggered_by="agent")

    assert [e["triggered_by"] for e in read_journal(journal)] == ["cli", "agent"]


def test_two_runs_in_the_same_second_get_different_ids(tmp_path):
    journal = tmp_path / "journal.jsonl"
    for _ in range(2):
        run_pipeline(DAY, steps=("ingest",), journal=journal, step_functions={"ingest": ok()})

    first, second = read_journal(journal)
    assert first["run_id"] != second["run_id"]


def test_stale_dbt_results_are_never_reported(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    (target / "run_results.json").write_text(json.dumps({"results": [
        {"unique_id": "model.old", "status": "success"}]}), encoding="utf-8")

    def crash_before_results(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=2, stdout="Parsing Error", stderr="")

    monkeypatch.setattr(run.subprocess, "run", crash_before_results)
    result = run_dbt("run", project=tmp_path)

    assert result["ok"] is False
    assert result["details"]["dbt_results"] == []
