"""The reference a replayed benchmark is held to, in the CI and anywhere else.

In replay the model's answers are frozen, so the score can only move if the code around the model
changed: tools, verdict validation, guardrail, policy or scoring. The reference is what the replay
gave for every scenario when it was last accepted. Any difference fails, in either direction: a
score that improves on its own is as suspect as one that drops, since a scoring bug can hide
dangerous cases. Moving the reference is a deliberate commit, visible in its diff.

    python -m pytest bench --replay                   # fails if the replay departs from the reference
    python -m bench.reference update logs/bench/RUN   # accept a finished replay run as the reference
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bench.runner import load_scenarios, summarize

REFERENCE = Path(__file__).parent / "reference.json"
# Everything a replay decides, per scenario. The duration is left out: it depends on the machine.
FIELDS = ("trap", "unseen", "expected_causes", "expected_decision", "causes", "decision", "stopped",
          "cell", "cause_correct", "decision_correct", "imposed_escalation", "right_category_wrong_action",
          "guardrail", "model_calls", "tool_calls", "prompt_tokens", "output_tokens")
HEADLINE = ("dangerous", "dangerous_executed", "right_decisions_blocked", "causes_correct",
            "decisions_correct", "traps_passed", "guardrail_refusals", "imposed_escalations",
            "right_category_wrong_action", "model_errors")


def build(records: list[dict], source: str) -> dict:
    summary = summarize(records)
    return {"source": source,
            "headline": {key: len(summary[key]) if isinstance(summary[key], list) else summary[key]
                         for key in HEADLINE},
            "scenarios": {r["id"]: {field: r.get(field) for field in FIELDS}
                          for r in sorted(records, key=lambda r: r["id"])}}


def differences(reference: dict, records: list[dict]) -> list[str]:
    """Every way the records depart from the reference, headline first; empty if they match."""
    current = build(records, source="")
    lines = [f"{key}: {expected} in the reference, {current['headline'][key]} now"
             for key, expected in reference["headline"].items() if current["headline"].get(key) != expected]
    expected, got = reference["scenarios"], current["scenarios"]
    for scenario in sorted(expected.keys() | got.keys()):
        if scenario not in got:
            lines.append(f"{scenario}: in the reference, not played")
        elif scenario not in expected:
            lines.append(f"{scenario}: played, not in the reference")
        else:
            lines += [f"{scenario}: {field} {expected[scenario].get(field)!r} in the reference, "
                      f"{got[scenario][field]!r} now"
                      for field in FIELDS if expected[scenario].get(field) != got[scenario][field]]
    return lines


def load(path: Path = REFERENCE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def update(run_dir: Path, path: Path = REFERENCE) -> dict:
    """Accept a finished replay run as the reference, if it played every scenario."""
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("kind") != "replay":
        raise ValueError(f"{run_dir.as_posix()} is a {metadata.get('kind')!r} run: the reference is "
                         "what a replay gives (python -m pytest bench --replay)")
    records = [json.loads(p.read_text(encoding="utf-8"))["record"]
               for p in sorted(run_dir.glob("[0-9]*.json"))]
    missing = sorted({s["id"] for s in load_scenarios()} - {r["id"] for r in records})
    if missing:
        raise ValueError(f"{run_dir.as_posix()} did not play {', '.join(missing)}")
    reference = build(records, run_dir.as_posix())
    path.write_text(json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return reference


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.reference")
    commands = parser.add_subparsers(dest="command", required=True)
    updating = commands.add_parser("update", help="accept a finished replay run as the reference")
    updating.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    reference = update(args.run_dir)
    print(f"{REFERENCE.as_posix()} written from {args.run_dir.as_posix()}: {reference['headline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
