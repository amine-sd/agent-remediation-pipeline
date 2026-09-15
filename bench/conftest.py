import json
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.loop import MODEL
from agent.ollama import OLLAMA_URL
from bench.runner import format_summary, summarize

RESULTS = Path("logs/bench")
_summary: dict = {}


def _check_model_server() -> None:
    """Stop before the first scenario if the model cannot answer: otherwise every scenario would
    end in a model error, and the run would measure nothing (seen three times on 15/09)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as response:
            installed = [m["name"] for m in json.loads(response.read())["models"]]
    except OSError as exc:
        pytest.exit(f"Ollama is not reachable at {OLLAMA_URL} ({exc}): nothing would be measured",
                    returncode=1)
    if MODEL not in installed:
        pytest.exit(f"model {MODEL!r} is not installed in Ollama (installed: {installed})", returncode=1)


@pytest.fixture(scope="session")
def bench_run():
    """One folder per benchmark run: every scenario's transcript, journal, decision and ticket."""
    _check_model_server()
    run = SimpleNamespace(dir=RESULTS / f"{datetime.now():%Y%m%dT%H%M%S}", records=[])
    run.dir.mkdir(parents=True, exist_ok=True)
    yield run
    _summary.update(summarize(run.records))
    (run.dir / "summary.json").write_text(json.dumps(_summary, ensure_ascii=False, indent=2),
                                          encoding="utf-8")


def pytest_terminal_summary(terminalreporter):
    if _summary:
        terminalreporter.section("benchmark score")
        for line in format_summary(_summary):
            terminalreporter.write_line(line)
