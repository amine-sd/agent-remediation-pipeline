"""The guardrail: the only door between the agent's verdict and anything that changes the pipeline.

The investigation only reads. Once the verdict is in, execute() applies its decision:
rerun_ingestion runs the ingestion again, but only if it is the first rerun for that day; close
changes nothing; escalate opens a ticket. Whatever the guardrail refuses becomes a ticket too, and
every decision, executed or refused, is written to logs/decisions.jsonl with its time and its
justification (docs/politique-autonomie.md).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from agent.tools import TICKETS, open_ticket, rerun_step
from pipeline.collect_context import read_journal
from pipeline.run import JOURNAL

DECISIONS_LOG = Path("logs/decisions.jsonl")
WHITELIST = ("ingest",)  # the only step the agent may rerun
MAX_RERUNS_PER_INCIDENT = 1  # an incident is one data date


def reruns_so_far(data_date: str, journal: Path = JOURNAL) -> int:
    """How many agent reruns the journal already holds for this day."""
    if not journal.exists():
        return 0
    return len({e["run_id"] for e in read_journal(journal)
                if e.get("triggered_by") == "agent" and e.get("data_date") == data_date})


def guarded_rerun(step: str, data_date: str, journal: Path = JOURNAL,
                  rerun: Callable[..., dict] = rerun_step) -> dict:
    """Run a step again if the policy allows it, otherwise say why not.

    The rerun function itself accepts any step: restricting here, and only here, is what lets a
    test prove that a refusal really happens."""
    if step not in WHITELIST:
        return {"allowed": False,
                "reason": f"rerunning {step!r} is not on the whitelist (only: {', '.join(WHITELIST)})"}
    if reruns_so_far(data_date, journal) >= MAX_RERUNS_PER_INCIDENT:
        return {"allowed": False,
                "reason": f"the ingestion of {data_date} was already rerun once for this incident"}
    return {"allowed": True, "rerun": rerun(step, data_date=data_date, journal=journal)}


def _ticket(causes: list[str], justification: str, proposed_action: str, tickets: Path) -> str:
    return open_ticket(causes, justification, proposed_action, tickets=tickets)["ticket_id"]


def execute(result: dict, data_date: str, incident_run_id: str, journal: Path = JOURNAL,
            decisions: Path = DECISIONS_LOG, tickets: Path = TICKETS,
            rerun: Callable[..., dict] = rerun_step) -> dict:
    """Apply the verdict of one agent run (as run_agent returns it) and journal the decision."""
    verdict = result.get("verdict")
    record = {"decided_at": datetime.now().isoformat(timespec="seconds"), "data_date": data_date,
              "incident_run_id": incident_run_id, "stopped": result["stopped"],
              "causes": verdict["causes"] if verdict else None,
              "decision": verdict["decision"] if verdict else None,
              "justification": verdict["justification"] if verdict else None}

    if verdict is None:
        # Budget spent, invalid verdict or silent model: the agent did not decide, a human must.
        reason = f"no valid verdict ({result['stopped']}): {result.get('error') or 'no detail'}"
        record.update(outcome="imposed_escalation", reason=reason,
                      ticket=_ticket(["unknown"], reason, "Investigate: the agent could not conclude.",
                                     tickets))
    elif "unknown" in verdict["causes"] and verdict["decision"] != "escalate":
        reason = "the cause is unknown, and an unknown cause always leads to an escalation"
        record.update(outcome="refused", reason=reason,
                      ticket=_ticket(verdict["causes"], f"Refused by the guardrail: {reason}. "
                                     f"Agent's justification: {verdict['justification']}",
                                     verdict["proposed_action"], tickets))
    elif verdict["decision"] == "rerun_ingestion":
        attempt = guarded_rerun("ingest", data_date, journal, rerun)
        if attempt["allowed"]:
            record.update(outcome="executed", rerun=attempt["rerun"])
        else:
            record.update(outcome="refused", reason=attempt["reason"],
                          ticket=_ticket(verdict["causes"], f"Refused by the guardrail: "
                                         f"{attempt['reason']}. Agent's justification: "
                                         f"{verdict['justification']}",
                                         verdict["proposed_action"], tickets))
    elif verdict["decision"] == "close":
        record.update(outcome="closed")
    else:
        record.update(outcome="escalated",
                      ticket=_ticket(verdict["causes"], verdict["justification"],
                                     verdict["proposed_action"], tickets))

    decisions.parent.mkdir(parents=True, exist_ok=True)
    with decisions.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
