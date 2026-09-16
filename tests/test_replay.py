"""Recording and replaying the model's answers, without a model."""

import json

import pytest

from agent import loop
from bench.replay import (ReplayChat, ReplayMismatch, extract, fingerprint, recording_agent,
                          replay_agent, write_fixture)

VERDICT = {"causes": ["source_error"], "justification": "HTTP 500 at the source.",
           "decision": "rerun_ingestion", "proposed_action": "Rerun the ingestion."}
USAGE = {"model_calls": 3, "prompt_tokens": 3000, "output_tokens": 300}


def responses():
    return [{"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "read_logs", "arguments": {}}}]},
            {"role": "assistant", "content": "The source answered with an error."},
            {"role": "assistant", "content": json.dumps(VERDICT)}]


def fixture(**changes):
    recorded = {"scenario": "06-source-error", "fingerprint": fingerprint(), "model": "qwen2.5:3b",
                "options": {}, "recorded_from": "test", "stopped": "answered",
                "responses": responses(), "usage": USAGE}
    recorded.update(changes)
    return recorded


@pytest.fixture(autouse=True)
def no_real_tools(monkeypatch):
    monkeypatch.setattr(loop, "call_tool", lambda name, args: {"runs": []})


def test_a_replay_serves_the_recorded_answers_and_keeps_the_recorded_cost(tmp_path):
    write_fixture("06-source-error", responses(), USAGE, "test", "answered", tmp_path)

    result = replay_agent("06-source-error", tmp_path)("alert")

    assert (result["stopped"], result["verdict"]) == ("answered", VERDICT)
    assert result["usage"]["model_calls"] == 3 and result["usage"]["prompt_tokens"] == 3000


def test_a_changed_prompt_refuses_to_replay():
    with pytest.raises(ReplayMismatch, match="record again"):
        ReplayChat(fixture(fingerprint="0" * 16))


def test_asking_for_more_answers_than_recorded_fails():
    chat = ReplayChat(fixture(responses=responses()[:1], stopped="budget"))
    chat(None, [], [], {})

    with pytest.raises(ReplayMismatch, match="only 1 were recorded"):
        chat(None, [], [], {})


def test_leaving_recorded_answers_unused_fails():
    chat = ReplayChat(fixture())
    chat(None, [], [], {})

    with pytest.raises(ReplayMismatch, match="the loop used 1"):
        chat.check_consumed()


def test_the_verdict_must_come_where_it_was_recorded():
    with pytest.raises(ReplayMismatch, match="the loop asked for the verdict"):
        ReplayChat(fixture())(None, [], [], {}, format={"type": "object"})


def test_a_mismatch_inside_the_loop_is_not_mistaken_for_a_model_error(tmp_path):
    write_fixture("06-source-error", responses()[:2], USAGE, "test", "answered", tmp_path)

    with pytest.raises(ReplayMismatch):
        replay_agent("06-source-error", tmp_path)("alert")


def test_a_missing_fixture_says_to_record_first(tmp_path):
    with pytest.raises(ReplayMismatch, match="record them first"):
        replay_agent("99-nothing", tmp_path)


def test_recording_keeps_the_answers_the_model_gave(tmp_path):
    served = iter(responses())

    def model(name, messages, tools, options, format=None):
        return {"message": next(served), "prompt_eval_count": 1000, "eval_count": 100}

    recording_agent("06-source-error", tmp_path, chat=model)("alert")

    recorded = json.loads((tmp_path / "06-source-error.json").read_text(encoding="utf-8"))
    assert recorded["responses"] == responses() and recorded["fingerprint"] == fingerprint()
    assert recorded["usage"] == USAGE


def transcript(tmp_path, stopped="answered", system=None):
    run = tmp_path / "run"
    run.mkdir()
    answers = responses()
    messages = [{"role": "system", "content": system or loop.SYSTEM_PROMPT},
                {"role": "user", "content": "alert"}, answers[0], {"role": "tool", "content": "{}"},
                answers[1], {"role": "user", "content": loop.VERDICT_REQUEST}, answers[2]]
    (run / "06-source-error.json").write_text(json.dumps(
        {"record": {"id": "06-source-error"}, "stopped": stopped, "messages": messages, "usage": USAGE}),
        encoding="utf-8")
    return run


def test_fixtures_can_be_extracted_from_a_finished_run(tmp_path):
    (path,) = extract(transcript(tmp_path), tmp_path / "fixtures")

    assert json.loads(path.read_text(encoding="utf-8"))["responses"] == responses()


def test_a_run_made_with_another_prompt_cannot_be_extracted(tmp_path):
    with pytest.raises(ReplayMismatch, match="another system prompt"):
        extract(transcript(tmp_path, system="an older prompt"), tmp_path / "fixtures")


def test_a_model_error_has_nothing_to_extract(tmp_path):
    with pytest.raises(ReplayMismatch, match="nothing to replay"):
        extract(transcript(tmp_path, stopped="model_error"), tmp_path / "fixtures")
