"""The agent loop, written by hand.

Investigation: send the conversation and the tool schemas to the model; if it asks for tools,
run them and send their answers back, until it answers in plain text or the budget of tool
calls is spent. Verdict: one last turn without tools, where Ollama constrains the answer to the
verdict schema; the answer is then checked again by our own validator. This is what an agent
framework would do in its place, except that here every step is visible and measured.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from agent.ollama import chat as ollama_chat
from agent.tools import SCHEMAS, call_tool
from agent.verdict import VERDICT_SCHEMA, InvalidVerdict, validate

MODEL = "qwen2.5:3b"
# temperature 0 and a fixed seed: measured runs must be reproducible (docs/mesures.md).
# num_ctx 4096: the model is shared with another project that loaded it with this context;
# asking for another size would make Ollama reload it at every switch between projects.
OPTIONS = {"temperature": 0, "seed": 0, "num_ctx": 4096}
TOOL_BUDGET = 10
MAX_TOOL_RESULT_CHARS = 2500
# Until the guardrails exist, the agent only reads: no rerun, no ticket (autonomy policy).
READ_ONLY_TOOLS = ("read_logs", "query_warehouse", "read_lineage", "compare_with_previous_run")

# What the agent is told of the autonomy policy: its principle and its whitelist, not the
# decision table, so that the decision measures its judgment (docs/politique-autonomie.md).
POLICY = """Your authority is limited. You may act alone in two ways only: rerun the ingestion step,
once per incident, when a rerun can plausibly fix the problem; or close the incident when nothing
is wrong. In every other case, and whenever you are unsure, escalate to a human."""

SYSTEM_PROMPT = f"""You are the on-call data engineer for a small data pipeline.

The pipeline runs once a day. It downloads the French fuel prices published for one day (one row
per station and fuel), lands them as CSV files, and builds DuckDB tables with dbt: stg_prices and
stg_stations, then fct_prices, dim_stations and agg_prices_by_department.

After each run you receive a short alert. Investigate with the tools before concluding: read the
journal, query the warehouse, read the lineage, compare the day with the previous one. The tools
only read: you cannot change anything during the investigation.

{POLICY}

When you have enough evidence, say briefly what you found. You will then be asked for a verdict."""

VERDICT_REQUEST = """Now give your verdict as one JSON object with these fields:
- causes: the root causes, from this list:
  schema_drift (a column was renamed or removed upstream),
  null_spike (a key column arrives empty),
  duplicate_rows (rows are duplicated),
  freshness (the day's file did not arrive),
  unit_drift (values changed unit or scale without breaking anything),
  source_error (the source answered with an error),
  none (nothing is wrong), unknown (you cannot tell).
- justification: the facts you read with the tools that support these causes.
- decision: rerun_ingestion or close (you act alone), or escalate (a human takes over).
- proposed_action: what should be done next."""


def _dbt_node(node: dict) -> str:
    parts = node["node"].split(".")
    name = parts[2] if len(parts) > 2 else node["node"]
    if node.get("failures"):
        return f"{name} ({node['failures']} failing rows)"
    return f"{name} ({node['status']}: {node.get('message')})"


def alert(view: dict) -> str:
    """What an on-call engineer would see first: the run's status, its steps, what did not pass.
    `view` is one run as read_logs returns it."""
    failed = any(step["status"] == "failed" for step in view["steps"])
    lines = [f"Daily pipeline run for data date {view['data_date']} (run {view['run_id']}): "
             f"{'FAILED' if failed else 'succeeded'}."]
    for step in view["steps"]:
        line = f"- {step['step']}: {step['status']}"
        if step.get("error"):
            line += f", error: {step['error']}"
        details = step.get("details") or {}
        if details.get("status") == "ingested":
            line += f", ingested {details['stations']} stations and {details['prices']} prices"
        elif details.get("status") == "skipped":
            line += f", nothing new ({details.get('reason')})"
        not_passing = (step.get("dbt") or {}).get("not_passing") or []
        if not_passing:
            line += ". Not passing: " + ", ".join(_dbt_node(n) for n in not_passing)
        lines.append(line)
    lines.append("Investigate with the tools, then give your diagnosis.")
    return "\n".join(lines)


def _run_tool(name: str, arguments) -> dict:
    if name not in READ_ONLY_TOOLS:
        return {"error": f"tool {name!r} is not available: you can only read"}
    if isinstance(arguments, str):  # some models send the arguments as a JSON string
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {"error": f"arguments are not valid JSON: {arguments!r}"}
    return call_tool(name, arguments)


class _Run:
    """The state of one agent run: the conversation, the tool calls made, the cost so far."""

    def __init__(self, alert_text: str, chat: Callable[..., dict], log: Callable[[str], None]):
        self.chat, self.log = chat, log
        self.schemas = [s for s in SCHEMAS if s["function"]["name"] in READ_ONLY_TOOLS]
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": alert_text}]
        self.trace: list[dict] = []
        self.usage = {"model_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "model_seconds": 0.0}
        self.notes = None

    def ask(self, tools: list[dict], format: dict | None = None) -> dict:
        started = time.monotonic()
        response = self.chat(MODEL, self.messages, tools, OPTIONS, format=format)
        seconds = time.monotonic() - started
        self.usage["model_calls"] += 1
        self.usage["prompt_tokens"] += response.get("prompt_eval_count", 0)
        self.usage["output_tokens"] += response.get("eval_count", 0)
        self.usage["model_seconds"] = round(self.usage["model_seconds"] + seconds, 1)
        self.messages.append(response["message"])
        self.log(f"model call {self.usage['model_calls']}: {seconds:.0f} s")
        return response["message"]

    def result(self, stopped: str, verdict: dict | None = None, error: str | None = None,
               raw_verdict: str | None = None) -> dict:
        # Rough size of what the model read on its last turn, to see whether the conversation
        # got close to num_ctx (Ollama drops the start of a conversation that does not fit).
        prompt_chars = len(json.dumps(self.messages, ensure_ascii=False)) + len(json.dumps(self.schemas))
        return {"verdict": verdict, "stopped": stopped, "error": error, "raw_verdict": raw_verdict,
                "notes": self.notes, "tool_calls": self.trace, "usage": self.usage,
                "prompt_chars": prompt_chars, "messages": self.messages}


def run_agent(alert_text: str, chat: Callable[..., dict] = ollama_chat, budget: int = TOOL_BUDGET,
              log: Callable[[str], None] = lambda line: None) -> dict:
    run = _Run(alert_text, chat, log)
    try:
        # Investigation.
        while True:
            message = run.ask(run.schemas)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                run.notes = message.get("content", "")
                break
            for tool_call in tool_calls:
                if len(run.trace) >= budget:
                    return run.result("budget")
                name = tool_call["function"]["name"]
                arguments = tool_call["function"].get("arguments") or {}
                answer = _run_tool(name, arguments)
                content = json.dumps(answer, ensure_ascii=False)
                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = content[:MAX_TOOL_RESULT_CHARS] + " ...[truncated]"
                run.trace.append({"tool": name, "arguments": arguments,
                                  "result_chars": len(content), "error": answer.get("error")})
                run.messages.append({"role": "tool", "tool_name": name, "content": content})
                log(f"  tool {name}({json.dumps(arguments, ensure_ascii=False)}) -> {len(content)} chars")

        # Verdict: no tools, and the answer constrained to the schema.
        run.messages.append({"role": "user", "content": VERDICT_REQUEST})
        raw = run.ask([], format=VERDICT_SCHEMA).get("content", "")
    except Exception as exc:
        # The model server failed (crash, lost connection, timeout): the agent could not decide.
        # Recorded as such, never mistaken for a verdict.
        return run.result("model_error", error=f"{type(exc).__name__}: {exc}")

    try:
        return run.result("answered", verdict=validate(raw), raw_verdict=raw)
    except InvalidVerdict as exc:
        return run.result("invalid_output", error=str(exc), raw_verdict=raw)
