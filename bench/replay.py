"""Record the model's answers during a benchmark run, and replay them without the model.

In replay, only the model is replaced: fault injection, pipeline, tools, verdict validation,
guardrail and scoring all run for real. A replay checks that the code around the model still turns
the same answers into the same scores, which is what the CI needs, without an LLM or a network. It
says nothing new about the model itself.

A fixture carries a fingerprint of everything the code sends the model besides the incident: system
prompt, verdict request, tool schemas and verdict schema. If one of them changes, or if the loop
asks for more or fewer answers than were recorded, the replay fails: the fixture no longer matches
the code and must be recorded again.

    python -m bench.replay extract logs/bench/RUN      # fixtures from a finished run's transcripts
    python -m pytest bench --record                    # record while running with the model
    python -m pytest bench --replay                    # replay, offline
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Callable

from agent import loop
from agent.loop import run_agent
from agent.ollama import chat as ollama_chat
from agent.tools import SCHEMAS
from agent.verdict import VERDICT_SCHEMA

FIXTURES = Path(__file__).parent / "fixtures"
VERDICT_TURN = ("answered", "invalid_output")  # runs that reached the verdict turn


class ReplayMismatch(RuntimeError):
    """The recorded answers no longer match the code: record them again."""


def fingerprint() -> str:
    schemas = [s for s in SCHEMAS if s["function"]["name"] in loop.READ_ONLY_TOOLS]
    material = json.dumps([loop.SYSTEM_PROMPT, loop.VERDICT_REQUEST, schemas, VERDICT_SCHEMA],
                          sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def write_fixture(scenario_id: str, responses: list[dict], usage: dict, source: str, stopped: str,
                  folder: Path = FIXTURES) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    fixture = {"scenario": scenario_id, "fingerprint": fingerprint(), "model": loop.MODEL,
               "options": loop.OPTIONS, "recorded_from": source, "stopped": stopped,
               "responses": responses,
               "usage": {key: usage.get(key) for key in ("model_calls", "prompt_tokens", "output_tokens")}}
    path = folder / f"{scenario_id}.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class ReplayChat:
    """Stands where Ollama stands and serves the recorded answers in order, checking on the way that
    the code still asks for them the same way."""

    def __init__(self, fixture: dict):
        if fixture["fingerprint"] != fingerprint():
            raise ReplayMismatch(f"{fixture['scenario']}: the prompt or the schemas changed since the "
                                 f"recording (fingerprint {fixture['fingerprint']}, now {fingerprint()}): "
                                 "record again")
        self.fixture, self.calls = fixture, 0

    def __call__(self, model, messages, tools, options, format=None) -> dict:
        responses, scenario = self.fixture["responses"], self.fixture["scenario"]
        if self.calls >= len(responses):
            raise ReplayMismatch(f"{scenario}: the loop asked for answer {self.calls + 1}, "
                                 f"only {len(responses)} were recorded")
        recorded_verdict = self.fixture["stopped"] in VERDICT_TURN and self.calls == len(responses) - 1
        asked_verdict = format is not None
        if recorded_verdict != asked_verdict:
            recorded = "the verdict" if recorded_verdict else "an investigation turn"
            asked = "the verdict" if asked_verdict else "an investigation turn"
            raise ReplayMismatch(f"{scenario}: answer {self.calls + 1} was recorded as {recorded}, "
                                 f"the loop asked for {asked}")
        response = responses[self.calls]
        self.calls += 1
        return {"message": dict(response), "prompt_eval_count": 0, "eval_count": 0}

    def check_consumed(self) -> None:
        recorded = len(self.fixture["responses"])
        if self.calls != recorded:
            raise ReplayMismatch(f"{self.fixture['scenario']}: {recorded} answers recorded, "
                                 f"the loop used {self.calls}")


def replay_agent(scenario_id: str, folder: Path = FIXTURES) -> Callable[[str], dict]:
    path = folder / f"{scenario_id}.json"
    if not path.exists():
        raise ReplayMismatch(f"no recorded answers for {scenario_id} ({path.as_posix()}): "
                             "record them first")
    fixture = json.loads(path.read_text(encoding="utf-8"))

    def agent(alert_text: str) -> dict:
        chat = ReplayChat(fixture)
        result = run_agent(alert_text, chat=chat)
        # The loop turns any exception into a model error; in replay there is no model to fail,
        # so a model error can only be a mismatch, and is raised as one.
        if result["stopped"] == "model_error":
            raise ReplayMismatch(f"{scenario_id}: {result['error']}")
        chat.check_consumed()
        # Replaying costs nothing: the cost kept is the one measured when the answers were recorded.
        result["usage"].update({k: fixture["usage"][k] for k in ("prompt_tokens", "output_tokens")})
        return result
    return agent


class RecordingChat:
    """Calls the real model and keeps each answer, in order."""

    def __init__(self, chat: Callable[..., dict] = ollama_chat):
        self.chat, self.responses = chat, []

    def __call__(self, model, messages, tools, options, format=None) -> dict:
        response = self.chat(model, messages, tools, options, format=format)
        self.responses.append(response["message"])
        return response


def recording_agent(scenario_id: str, folder: Path = FIXTURES,
                    chat: Callable[..., dict] = ollama_chat) -> Callable[[str], dict]:
    def agent(alert_text: str) -> dict:
        recorder = RecordingChat(chat)
        result = run_agent(alert_text, chat=recorder)
        if result["stopped"] == "model_error":
            raise RuntimeError(f"{scenario_id}: model error while recording ({result['error']}), "
                               "nothing recorded")
        write_fixture(scenario_id, recorder.responses, result["usage"], "live recording",
                      result["stopped"], folder)
        return result
    return agent


def extract(run_dir: Path, folder: Path = FIXTURES) -> list[Path]:
    """Fixtures from the transcripts of a finished run, if the current prompt produced them."""
    paths = []
    for path in sorted(run_dir.glob("[0-9]*.json")):
        transcript = json.loads(path.read_text(encoding="utf-8"))
        scenario_id, messages = transcript["record"]["id"], transcript["messages"]
        if transcript["stopped"] == "model_error":
            raise ReplayMismatch(f"{scenario_id}: the run ended in a model error, nothing to replay")
        if messages[0]["content"] != loop.SYSTEM_PROMPT:
            raise ReplayMismatch(f"{scenario_id}: recorded with another system prompt")
        requests = [m["content"] for m in messages[2:] if m["role"] == "user"]
        if transcript["stopped"] in VERDICT_TURN and requests[-1:] != [loop.VERDICT_REQUEST]:
            raise ReplayMismatch(f"{scenario_id}: recorded with another verdict request")
        responses = [m for m in messages if m["role"] == "assistant"]
        if len(responses) != transcript["usage"]["model_calls"]:
            raise ReplayMismatch(f"{scenario_id}: {len(responses)} answers in the transcript, "
                                 f"{transcript['usage']['model_calls']} model calls counted")
        paths.append(write_fixture(scenario_id, responses, transcript["usage"], run_dir.as_posix(),
                                   transcript["stopped"], folder))
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.replay")
    commands = parser.add_subparsers(dest="command", required=True)
    extracting = commands.add_parser("extract", help="write fixtures from a finished run")
    extracting.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    paths = extract(args.run_dir)
    print(f"{len(paths)} fixtures written in {FIXTURES.as_posix()} (fingerprint {fingerprint()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
