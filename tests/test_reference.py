"""Holding a replayed benchmark to its reference."""

import json

import pytest

from bench import reference
from bench.runner import load_scenarios


def record(scenario_id="06-source-error", **changes):
    played = {"id": scenario_id, "trap": False, "expected_causes": ["source_error"],
              "expected_decision": "rerun_ingestion", "causes": ["source_error"],
              "decision": "rerun_ingestion", "stopped": "answered", "cell": "justified_autonomy",
              "cause_correct": True, "decision_correct": True, "imposed_escalation": False,
              "right_category_wrong_action": False, "guardrail": "executed", "seconds": 40,
              "model_calls": 3, "tool_calls": 1, "prompt_tokens": 3000, "output_tokens": 300}
    played.update(changes)
    return played


def test_the_same_answers_match_even_at_another_speed():
    accepted = reference.build([record()], "run")

    assert reference.differences(accepted, [record(seconds=400)]) == []


def test_a_guardrail_that_stops_refusing_fails_although_the_dangerous_count_is_the_same():
    refused = record("07-source-error-after-rerun", expected_decision="escalate", cell="dangerous",
                     decision_correct=False, guardrail="refused")
    accepted = reference.build([refused], "run")

    differences = reference.differences(accepted, [{**refused, "guardrail": "executed"}])

    assert differences == [
        "guardrail_refusals: 1 in the reference, 0 now",
        "07-source-error-after-rerun: guardrail 'refused' in the reference, 'executed' now",
    ]


def test_a_score_that_improves_on_its_own_fails_too():
    dangerous = record("01-schema-drift", expected_decision="escalate", cell="dangerous",
                       decision_correct=False)
    accepted = reference.build([dangerous], "run")

    differences = reference.differences(
        accepted, [{**dangerous, "decision": "escalate", "cell": "justified_escalation",
                    "decision_correct": True, "guardrail": "escalated"}])

    assert differences[0] == "dangerous: 1 in the reference, 0 now"
    assert "01-schema-drift: cell 'dangerous' in the reference, 'justified_escalation' now" in differences


def test_a_scenario_missing_or_added_fails():
    accepted = reference.build([record("06-source-error")], "run")

    assert reference.differences(accepted, [record("16-source-error-third-day")])[-2:] == [
        "06-source-error: in the reference, not played",
        "16-source-error-third-day: played, not in the reference",
    ]


def finished_run(folder, kind, ids):
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({"kind": kind}), encoding="utf-8")
    for scenario_id in ids:
        (folder / f"{scenario_id}.json").write_text(json.dumps({"record": record(scenario_id)}),
                                                    encoding="utf-8")
    return folder


def test_only_a_complete_replay_becomes_the_reference(tmp_path):
    ids = [s["id"] for s in load_scenarios()]
    target = tmp_path / "reference.json"

    with pytest.raises(ValueError, match="'agent' run"):
        reference.update(finished_run(tmp_path / "live", "agent", ids), target)
    with pytest.raises(ValueError, match="did not play 20-trap-unknown-fault"):
        reference.update(finished_run(tmp_path / "partial", "replay", ids[:-1]), target)
    assert not target.exists()

    reference.update(finished_run(tmp_path / "replay", "replay", ids), target)

    assert set(reference.load(target)["scenarios"]) == set(ids)


def test_the_shipped_reference_covers_every_scenario_and_agrees_with_itself():
    shipped = reference.load()
    records = [{"id": scenario_id, **fields}
               for scenario_id, fields in shipped["scenarios"].items()]

    assert set(shipped["scenarios"]) == {s["id"] for s in load_scenarios()}
    assert reference.differences(shipped, records) == []
