"""The benchmark: one test per scenario of bench/scenarios/.

    python -m pytest bench                        # the agent, with the model
    python -m pytest bench --record               # the same, recording the model's answers
    python -m pytest bench --replay               # the recorded answers: no model, no network
    BENCH_AGENT=baseline python -m pytest bench   # the rules without a model

A scenario test fails only if its scenario could not be played (injection, pipeline, reset, or a
replay that no longer matches the code). A wrong answer is a result, recorded and scored, never a
failure. In replay only, a last test holds the whole run to bench/reference.json: that is what fails
the CI (docs/mesures.md). The score is printed at the end and saved in logs/bench/.
"""

import pytest

from agent.loop import run_agent
from bench import reference
from bench.baseline import rules_agent
from bench.replay import recording_agent, replay_agent
from bench.runner import load_scenarios, run_scenario

SCENARIOS = load_scenarios()


def agent_for(scenario_id: str, mode: str):
    if mode == "baseline":
        return rules_agent
    if mode == "replay":
        return replay_agent(scenario_id)
    if mode == "record":
        return recording_agent(scenario_id)
    return run_agent


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario(scenario, bench_run):
    agent = agent_for(scenario["id"], bench_run.mode)
    bench_run.records.append(run_scenario(scenario, bench_run.dir, agent=agent))


def test_the_replay_matches_the_reference(bench_run):
    """Runs after the scenarios, since pytest keeps the order of the file. Only a replay is held to
    the reference: with the model, the answers change from one run to the next."""
    if bench_run.mode != "replay":
        pytest.skip("only a replay is compared with bench/reference.json")
    differences = reference.differences(reference.load(), bench_run.records)
    assert not differences, ("the replay departs from bench/reference.json:\n" + "\n".join(differences)
                             + "\nIf the change is intended: python -m bench.reference update logs/bench/RUN")
