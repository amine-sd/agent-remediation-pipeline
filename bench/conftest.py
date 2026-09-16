import json
import os
import platform
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.loop import MODEL, OPTIONS
from agent.ollama import OLLAMA_URL
from bench.replay import FIXTURES
from bench.runner import format_summary, summarize

RESULTS = Path("logs/bench")
_summary: dict = {}


def pytest_addoption(parser):
    group = parser.getgroup("benchmark")
    group.addoption("--replay", action="store_true",
                    help="replay the model answers recorded in bench/fixtures: no model, no network")
    group.addoption("--record", action="store_true",
                    help="run with the model and record its answers in bench/fixtures")


def _get(path: str):
    with urllib.request.urlopen(f"{OLLAMA_URL}{path}", timeout=10) as response:
        return json.loads(response.read())


def _model_server() -> dict:
    """Stop before the first scenario if the model cannot answer: otherwise every scenario would
    end in a model error, and the run would measure nothing (seen three times on 15/09). Return
    what the report needs to state the conditions of the run (docs/mesures.md)."""
    try:
        installed = _get("/api/tags")["models"]
        version = _get("/api/version")["version"]
    except OSError as exc:
        pytest.exit(f"Ollama is not reachable at {OLLAMA_URL} ({exc}): nothing would be measured",
                    returncode=1)
    model = next((m for m in installed if m["name"] == MODEL), None)
    if model is None:
        pytest.exit(f"model {MODEL!r} is not installed in Ollama "
                    f"(installed: {[m['name'] for m in installed]})", returncode=1)
    details = model.get("details", {})
    return {"ollama_version": version, "model": MODEL, "digest": model["digest"][:12],
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level")}


def _mode(config) -> str:
    replay, record = config.getoption("--replay"), config.getoption("--record")
    if replay and record:
        pytest.exit("--replay and --record cannot be used together", returncode=1)
    if os.environ.get("BENCH_AGENT") == "baseline":
        return "baseline"
    return "replay" if replay else "record" if record else "agent"


def _conditions(mode: str) -> dict:
    if mode == "baseline":  # the rules need no model
        return {"model": "baseline rules, no model"}
    if mode == "replay":  # no model is called: the conditions are those of the recording
        recorded = sorted(FIXTURES.glob("*.json"))
        if not recorded:
            pytest.exit(f"no recorded answers in {FIXTURES.as_posix()}: record them first", returncode=1)
        first = json.loads(recorded[0].read_text(encoding="utf-8"))
        return {"model": f"{first['model']} (replay of recorded answers)",
                "recorded_from": first["recorded_from"], "recorded_options": first["options"]}
    return _model_server()


@pytest.fixture(scope="session")
def bench_run(request):
    """One folder per benchmark run: every scenario's transcript, journal, decision and ticket,
    and the conditions of the run."""
    mode = _mode(request.config)
    conditions = _conditions(mode)
    run = SimpleNamespace(dir=RESULTS / f"{datetime.now():%Y%m%dT%H%M%S}", records=[], mode=mode)
    run.dir.mkdir(parents=True, exist_ok=True)
    metadata = {"kind": mode, **conditions, "options": OPTIONS,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "machine": platform.processor() or platform.machine()}
    (run.dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    yield run
    _summary.update(summarize(run.records))
    if mode in ("baseline", "replay"):
        _summary["model"] = conditions["model"]
    (run.dir / "summary.json").write_text(json.dumps(_summary, ensure_ascii=False, indent=2),
                                          encoding="utf-8")


def pytest_terminal_summary(terminalreporter):
    if _summary:
        terminalreporter.section("benchmark score")
        for line in format_summary(_summary):
            terminalreporter.write_line(line)
