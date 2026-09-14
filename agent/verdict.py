"""The agent's final answer: a verdict with a fixed shape, never free text.

The shape is written once, as a JSON schema, and used twice: given to the model so that its
answer follows it, and checked by validate() on what comes back. A verdict that does not pass
is rejected, and the autonomy policy then imposes an escalation (docs/politique-autonomie.md).
The validator is written by hand: twenty lines are cheaper than a dependency.
"""

from __future__ import annotations

import json

from agent.tools import CAUSES

DECISIONS = ["rerun_ingestion", "close", "escalate"]
# Answers that cannot be combined with another cause: "nothing is wrong" and "I do not know".
ALONE = ("none", "unknown")

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "causes": {"type": "array", "items": {"type": "string", "enum": CAUSES}, "minItems": 1,
                   "description": "Root causes found; several only if several faults are present."},
        "justification": {"type": "string",
                          "description": "The facts read with the tools that support the causes."},
        "decision": {"type": "string", "enum": DECISIONS,
                     "description": "rerun_ingestion or close: act alone; escalate: tell a human."},
        "proposed_action": {"type": "string",
                            "description": "What should be done next, by the agent or by a human."},
    },
    "required": ["causes", "justification", "decision", "proposed_action"],
    "additionalProperties": False,
}


class InvalidVerdict(ValueError):
    """The verdict does not follow the schema; the message lists every problem found."""


def validate(raw: str | dict) -> dict:
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidVerdict(f"not valid JSON: {exc}") from None
    else:
        data = raw
    if not isinstance(data, dict):
        raise InvalidVerdict("not a JSON object")

    fields = VERDICT_SCHEMA["properties"]
    problems = [f"missing field {k!r}" for k in VERDICT_SCHEMA["required"] if k not in data]
    problems += [f"unexpected field {k!r}" for k in data if k not in fields]

    causes = data.get("causes")
    if "causes" in data:
        if not isinstance(causes, list) or not causes:
            problems.append("causes must be a non-empty list")
        else:
            problems += [f"unknown cause {c!r}" for c in causes if c not in CAUSES]
            if len(set(map(str, causes))) != len(causes):
                problems.append("a cause is listed twice")
            problems += [f"{c!r} cannot be combined with another cause"
                         for c in ALONE if c in causes and len(causes) > 1]
    if "decision" in data and data["decision"] not in DECISIONS:
        problems.append(f"unknown decision {data['decision']!r}")
    for key in ("justification", "proposed_action"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            problems.append(f"{key} must be a non-empty text")

    if problems:
        raise InvalidVerdict("; ".join(problems))
    return data
