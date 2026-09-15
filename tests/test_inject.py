import json
from datetime import date

import pytest

import inject

DAY = date(2026, 9, 13)


@pytest.fixture
def env(tmp_path):
    raw = tmp_path / "raw"
    day = raw / "2026-09-13"
    day.mkdir(parents=True)
    (day / "prices.csv").write_text("pdv_id,prix_valeur\n1,2.3\n", encoding="utf-8")
    (day / "source.zip").write_bytes(b"clean archive")
    (raw / "2026-09-12").mkdir()
    rebuilds = []
    paths = {"raw_dir": raw, "switch": tmp_path / "faults" / "active.json",
             "backup_dir": tmp_path / "faults" / "backup", "rebuild": lambda: rebuilds.append(1)}
    return paths, rebuilds


def snapshot(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir()}


def test_inject_takes_the_day_out_and_arms_the_fault(env):
    paths, rebuilds = env
    before = snapshot(paths["raw_dir"] / "2026-09-13")

    inject.inject("null_spike", DAY, {"fraction": 0.3}, **paths)

    assert not (paths["raw_dir"] / "2026-09-13").exists()
    assert snapshot(paths["backup_dir"] / "2026-09-13") == before
    armed = json.loads(paths["switch"].read_text(encoding="utf-8"))
    assert armed["faults"] == [{"name": "null_spike", "params": {"fraction": 0.3}}]
    assert armed["date"] == "2026-09-13"
    assert rebuilds == [1]


def test_several_faults_can_be_armed_together_and_reset_at_once(env):
    paths, _ = env
    before = snapshot(paths["raw_dir"] / "2026-09-13")

    inject.inject([{"name": "unit_drift"}, {"name": "duplicate_rows"}], DAY, **paths)

    armed = json.loads(paths["switch"].read_text(encoding="utf-8"))
    assert [f["name"] for f in armed["faults"]] == ["unit_drift", "duplicate_rows"]
    assert inject.reset(**paths).startswith("reset: unit_drift, duplicate_rows removed")
    assert snapshot(paths["raw_dir"] / "2026-09-13") == before


def test_reset_puts_everything_back(env):
    paths, rebuilds = env
    before = snapshot(paths["raw_dir"] / "2026-09-13")
    inject.inject("duplicate_rows", DAY, **paths)
    faulty = paths["raw_dir"] / "2026-09-13"  # what the faulty run would have written
    faulty.mkdir()
    (faulty / "prices.csv").write_text("broken", encoding="utf-8")

    inject.reset(**paths)

    assert snapshot(paths["raw_dir"] / "2026-09-13") == before
    assert not paths["switch"].exists()
    assert not (paths["backup_dir"] / "2026-09-13").exists()
    assert rebuilds == [1, 1]


def test_a_second_fault_is_refused_until_reset(env):
    paths, _ = env
    inject.inject("schema_drift", DAY, **paths)

    with pytest.raises(RuntimeError, match="already armed"):
        inject.inject("null_spike", date(2026, 9, 12), **paths)


def test_reset_without_a_fault_does_nothing(env):
    paths, rebuilds = env

    assert inject.reset(**paths) == "no fault armed, nothing to reset"
    assert rebuilds == []


def test_latest_day_ignores_folders_that_are_not_days(env):
    paths, _ = env
    (paths["raw_dir"] / ".tmp").mkdir()

    assert inject.latest_day(paths["raw_dir"]) == DAY
