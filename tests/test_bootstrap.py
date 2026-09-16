"""Rebuilding the benchmark's data from the archives shipped in the repository."""

import io
import zipfile

from bench.bootstrap import DAYS, bootstrap
from bench.runner import load_scenarios


def tiny_archive():
    xml = (b'<?xml version="1.0" encoding="ISO-8859-1"?><pdv_liste>'
           b'<pdv id="1" latitude="4620100" longitude="519800" cp="01000" pop="R">'
           b'<adresse>A</adresse><ville>V</ville>'
           b'<prix nom="Gazole" id="1" maj="2026-09-13T08:00:00" valeur="2.3"/></pdv></pdv_liste>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("day.xml", xml)
    return buffer.getvalue()


def test_the_shipped_days_rebuild_the_raw_layer_once(tmp_path):
    days = tmp_path / "days"
    days.mkdir()
    (days / "2026-09-13.zip").write_bytes(tiny_archive())
    raw = tmp_path / "raw"

    first = bootstrap(days, raw, build=False)
    second = bootstrap(days, raw, build=False)

    assert first[0]["status"] == "ingested" and second[0]["status"] == "skipped"
    assert (raw / "2026-09-13" / "prices.csv").exists()


def test_every_day_the_scenarios_use_is_shipped():
    shipped = {archive.stem for archive in DAYS.glob("*.zip")}

    assert {scenario["day"] for scenario in load_scenarios()} <= shipped
