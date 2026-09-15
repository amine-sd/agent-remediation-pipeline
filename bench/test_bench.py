"""The benchmark: one test per scenario of bench/scenarios/.

    python -m pytest bench

A test fails only if its scenario could not be played (injection, pipeline, reset). A wrong answer
from the agent is a result, recorded and scored, never a failure: the threshold that fails the CI
comes later (docs/mesures.md). The score is printed at the end and saved in logs/bench/.
"""

import pytest

from bench.runner import load_scenarios, run_scenario

SCENARIOS = load_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario(scenario, bench_run):
    bench_run.records.append(run_scenario(scenario, bench_run.dir))
