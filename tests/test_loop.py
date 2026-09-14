import json

from agent import loop
from agent.loop import READ_ONLY_TOOLS, alert, run_agent
from agent.verdict import VERDICT_SCHEMA

VERDICT = {"causes": ["schema_drift"], "justification": "prix_valeur became valeur.",
           "decision": "escalate", "proposed_action": "Map the renamed column."}


def fake_chat(responses):
    """Stands in for Ollama: returns the given responses in order, and records what it was sent."""
    sent = []

    def chat(model, messages, tools, options, format=None):
        sent.append({"messages": [dict(m) for m in messages],
                     "tools": [t["function"]["name"] for t in tools], "format": format})
        return responses[len(sent) - 1]

    return chat, sent


def reply(content="", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"message": message, "prompt_eval_count": 100, "eval_count": 20}


def verdict_reply(verdict=VERDICT):
    return reply(json.dumps(verdict))


def call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


def test_the_loop_investigates_then_returns_a_validated_verdict(monkeypatch):
    executed = []
    monkeypatch.setattr(loop, "call_tool", lambda name, args: executed.append((name, args)) or {"rows": [[4]]})
    chat, sent = fake_chat([reply(tool_calls=[call("query_warehouse", {"sql": "select 1"})]),
                            reply("The price column was renamed upstream."),
                            verdict_reply()])

    result = run_agent("alert", chat=chat)

    assert (result["stopped"], result["verdict"]) == ("answered", VERDICT)
    assert result["notes"] == "The price column was renamed upstream."
    assert executed == [("query_warehouse", {"sql": "select 1"})]
    assert sent[1]["messages"][-1] == {"role": "tool", "tool_name": "query_warehouse",
                                       "content": '{"rows": [[4]]}'}
    assert result["usage"]["model_calls"] == 3 and result["usage"]["prompt_tokens"] == 300


def test_the_verdict_turn_has_no_tools_and_is_constrained_by_the_schema():
    chat, sent = fake_chat([reply("Nothing looks wrong."), verdict_reply()])

    run_agent("alert", chat=chat)

    assert sent[0]["tools"] == list(READ_ONLY_TOOLS) and sent[0]["format"] is None
    assert sent[1]["tools"] == [] and sent[1]["format"] == VERDICT_SCHEMA
    assert sent[1]["messages"][-1]["content"].startswith("Now give your verdict")


def test_an_invalid_verdict_is_rejected_and_kept_for_review():
    chat, _ = fake_chat([reply("Found it."), reply("It is a schema drift, escalate.")])

    result = run_agent("alert", chat=chat)

    assert (result["stopped"], result["verdict"]) == ("invalid_output", None)
    assert "not valid JSON" in result["error"]
    assert result["raw_verdict"] == "It is a schema drift, escalate."


def test_only_the_read_only_tools_are_offered_and_run(monkeypatch):
    executed = []
    monkeypatch.setattr(loop, "call_tool", lambda name, args: executed.append(name) or {})
    chat, sent = fake_chat([reply(tool_calls=[call("rerun_step", {"step": "ingest"})]),
                            reply("done"), verdict_reply()])

    run_agent("alert", chat=chat)

    assert executed == []
    assert "not available" in sent[1]["messages"][-1]["content"]


def test_the_budget_stops_an_agent_that_goes_round_in_circles(monkeypatch):
    monkeypatch.setattr(loop, "call_tool", lambda name, args: {})
    chat, _ = fake_chat([reply(tool_calls=[call("read_logs", {})])] * 20)

    result = run_agent("alert", chat=chat, budget=3)

    assert (result["stopped"], result["verdict"]) == ("budget", None)
    assert len(result["tool_calls"]) == 3


def test_a_model_failure_ends_the_run_without_a_verdict():
    def broken_chat(model, messages, tools, options, format=None):
        raise ConnectionError("Remote end closed connection without response")

    result = run_agent("alert", chat=broken_chat)

    assert (result["stopped"], result["verdict"]) == ("model_error", None)
    assert "Remote end closed" in result["error"]


def test_a_model_failure_during_the_verdict_turn_is_a_model_error():
    responses = iter([reply("Found it.")])

    def chat(model, messages, tools, options, format=None):
        if format is not None:
            raise TimeoutError("timed out")
        return next(responses)

    result = run_agent("alert", chat=chat)

    assert (result["stopped"], result["notes"]) == ("model_error", "Found it.")


def test_arguments_sent_as_a_json_string_are_decoded(monkeypatch):
    executed = []
    monkeypatch.setattr(loop, "call_tool", lambda name, args: executed.append(args) or {})
    chat, _ = fake_chat([reply(tool_calls=[call("read_logs", '{"runs": 2}')]), reply("ok"),
                         verdict_reply()])

    run_agent("alert", chat=chat)

    assert executed == [{"runs": 2}]


def test_the_alert_shows_what_an_on_call_engineer_would_see_first():
    view = {"run_id": "r2", "mode": "full", "data_date": "2026-09-13", "steps": [
        {"step": "ingest", "status": "success",
         "details": {"status": "ingested", "stations": 9922, "prices": 31159}},
        {"step": "transform", "status": "success", "dbt": {"nodes": 5, "not_passing": []}},
        {"step": "test", "status": "failed", "dbt": {"nodes": 13, "not_passing": [
            {"node": "test.fuel_prices.not_null_fct_prices_price_eur_per_liter.abc",
             "status": "fail", "failures": 31159, "message": "Got 31159 results"}]}},
    ]}

    text = alert(view)

    assert "data date 2026-09-13" in text and "FAILED" in text
    assert "ingested 9922 stations and 31159 prices" in text
    assert "not_null_fct_prices_price_eur_per_liter (31159 failing rows)" in text
