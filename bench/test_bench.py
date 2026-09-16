"""The benchmark: one test per scenario of bench/scenarios/.

    python -m pytest bench                        # the agent, with the model
    python -m pytest bench --record               # the same, recording the model's answers
    python -m pytest bench --replay               # the recorded answers: no model, no network
    BENCH_AGENT=baseline python -m pytest bench   # the rules without a model

A test fails only if its scenario could not be played (injection, pipeline, reset, or a replay that
no longer matches the code). A wrong answer is a result, recorded and scored, never a failure: the
threshold that fails the CI is set separately (docs/mesures.md). The score is printed at the end and
saved in logs/bench/.
"""

import pytest

from agent.loop import run_agent
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
