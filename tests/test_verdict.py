import json

import pytest

from agent.tools import CAUSES
from agent.verdict import VERDICT_SCHEMA, InvalidVerdict, validate

VALID = {"causes": ["schema_drift"],
         "justification": "prices.csv lost prix_valeur and gained valeur; every price is empty.",
         "decision": "escalate",
         "proposed_action": "Map the renamed column in staging, then rerun."}


def with_(**changes):
    verdict = dict(VALID, **changes)
    return {k: v for k, v in verdict.items() if v is not ...}


def test_a_valid_verdict_passes_as_text_or_as_a_dict():
    assert validate(json.dumps(VALID)) == VALID
    assert validate(VALID) == VALID


def test_two_simultaneous_causes_are_allowed():
    assert validate(with_(causes=["source_error", "unit_drift"]))["causes"] == ["source_error", "unit_drift"]


@pytest.mark.parametrize("raw, problem", [
    ("The price column was renamed.", "not valid JSON"),
    ('["schema_drift"]', "not a JSON object"),
    (with_(decision=...), "missing field 'decision'"),
    (with_(confidence=0.9), "unexpected field 'confidence'"),
    (with_(causes=[]), "non-empty list"),
    (with_(causes=["gremlins"]), "unknown cause 'gremlins'"),
    (with_(causes=["null_spike", "null_spike"]), "listed twice"),
    (with_(causes=["none", "unit_drift"]), "'none' cannot be combined"),
    (with_(causes=["unknown", "schema_drift"]), "'unknown' cannot be combined"),
    (with_(decision="fix_it"), "unknown decision 'fix_it'"),
    (with_(justification="  "), "justification must be a non-empty text"),
])
def test_an_invalid_verdict_is_rejected(raw, problem):
    raw = raw if isinstance(raw, str) else json.dumps(raw)

    with pytest.raises(InvalidVerdict, match=problem):
        validate(raw)


def test_every_problem_is_reported_at_once():
    with pytest.raises(InvalidVerdict) as error:
        validate({"causes": ["gremlins"], "decision": "fix_it"})

    message = str(error.value)
    assert "missing field 'justification'" in message
    assert "unknown cause 'gremlins'" in message
    assert "unknown decision 'fix_it'" in message


def test_the_schema_and_the_tools_share_the_same_causes():
    assert VERDICT_SCHEMA["properties"]["causes"]["items"]["enum"] == CAUSES
