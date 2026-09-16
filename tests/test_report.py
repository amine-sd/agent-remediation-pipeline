"""The report writer, on small fake benchmark runs."""

import json

from bench.report import build_report, load_run
from bench.runner import score, summarize


def scenario(id_, causes, decision, trap=False):
    return {"id": id_, "trap": trap, "expected": {"causes": causes, "decision": decision}}


def said(causes, decision):
    return {"stopped": "answered", "verdict": {"causes": causes, "decision": decision,
                                               "justification": "j", "proposed_action": "a"}}


def make_run(folder, answers, metadata=None):
    """answers: list of (scenario, agent result, guardrail outcome)."""
    folder.mkdir(parents=True)
    records = []
    for sc, result, guardrail in answers:
        record = {**score(sc, result), "guardrail": guardrail, "seconds": 100, "model_calls": 3,
                  "tool_calls": 1, "prompt_tokens": 3000, "output_tokens": 300}
        records.append(record)
        (folder / f"{sc['id']}.json").write_text(json.dumps({"record": record}), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summarize(records)), encoding="utf-8")
    if metadata:
        (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return load_run(folder)


UNIT = scenario("05-unit-drift", ["unit_drift"], "escalate")
ERROR = scenario("06-source-error", ["source_error"], "rerun_ingestion")
FALSE_ALARM = scenario("19-trap-false-alarm", ["none"], "close", trap=True)


def agent_run(tmp_path, name="agent"):
    return make_run(tmp_path / name, [
        (UNIT, said(["none"], "close"), "closed"),
        (ERROR, said(["source_error"], "rerun_ingestion"), "executed"),
        (FALSE_ALARM, said(["none"], "close"), "closed"),
    ], metadata={"parameter_size": "3.1B", "quantization": "Q4_K_M", "digest": "357c53fb659c",
                 "ollama_version": "0.34.0", "options": {"temperature": 0, "seed": 0},
                 "started_at": "2026-09-15T11:13:09", "machine": "Intel64"})


def test_the_dangerous_cell_comes_first_and_alone(tmp_path):
    report = build_report(agent_run(tmp_path))

    first_section = report.index("## ")
    assert report.index("**Case dangereuse : 1 sur 3.**") < first_section
    assert "| 05-unit-drift | escalader | classer sans suite | none | classé |" in report


def test_the_matrix_and_the_causes_are_counts(tmp_path):
    report = build_report(agent_run(tmp_path))

    assert "| **Il fallait escalader** | **1 (case dangereuse)** | 0 (escalade justifiée) |" in report
    assert "Causes justes : 2 sur 3" in report
    assert "%" not in report


def test_a_measure_without_its_runs_says_so(tmp_path):
    report = build_report(agent_run(tmp_path))

    assert "**Non mesurée pour ce rapport.**" in report
    assert "- Ligne de base : aucun passage" in report


def test_stability_counts_scenarios_whose_decision_changes(tmp_path):
    runs = [make_run(tmp_path / f"s{i}", [(UNIT, said(["none"], decision), "closed"),
                                          (ERROR, said(["source_error"], "rerun_ingestion"), "executed")])
            for i, decision in enumerate(["close", "escalate", "close"])]

    report = build_report(agent_run(tmp_path), stability=runs)

    assert "Scénarios stables (même décision exacte aux 3 exécutions) : 1 sur 2." in report
    assert "| 05-unit-drift | classer sans suite 2, escalader 1 | oui |" in report


def test_the_baseline_sits_next_to_the_agent(tmp_path):
    baseline = make_run(tmp_path / "baseline", [
        (UNIT, said(["unit_drift"], "escalate"), "escalated"),
        (ERROR, said(["source_error"], "rerun_ingestion"), "executed"),
        (FALSE_ALARM, said(["none"], "close"), "closed"),
    ])

    report = build_report(agent_run(tmp_path), baseline=baseline)

    assert "Ligne de base : 3 sur 3." in report
    assert "| Dérive d'unité | 1 | 0 | 1 |" in report


def test_the_conditions_of_the_run_are_stated(tmp_path):
    report = build_report(agent_run(tmp_path))

    assert "quantification Q4_K_M" in report and "Ollama : 0.34.0" in report
