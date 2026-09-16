import io
import urllib.error
import zipfile
from datetime import date

import pytest

from pipeline import faults
from pipeline.ingest import (PRICE_COLUMNS, STATION_COLUMNS, SourceUnavailable, fetch_archive,
                             ingest_day)

DAY = date(2026, 9, 13)


def tables(n=10):
    prices = [{"pdv_id": str(i), "prix_id": "1", "prix_nom": "Gazole",
               "prix_maj": "2026-09-13T08:00:00", "prix_valeur": "2.3"} for i in range(n)]
    return {"stations.csv": (list(STATION_COLUMNS), []), "prices.csv": (list(PRICE_COLUMNS), prices)}


def archive():
    xml = (b'<?xml version="1.0" encoding="ISO-8859-1"?><pdv_liste>'
           b'<pdv id="1" latitude="4620100" longitude="519800" cp="01000" pop="R">'
           b'<adresse>A</adresse><ville>V</ville>'
           b'<prix nom="Gazole" id="1" maj="2026-09-13T08:00:00" valeur="2.3"/></pdv></pdv_liste>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("day.xml", xml)
    return buffer.getvalue()


def armed(name):
    return {"name": name, "date": "2026-09-13", "params": {}}


def test_schema_drift_renames_the_price_column_and_keeps_the_values():
    columns, rows = faults.rename_price_column(tables(), {})["prices.csv"]

    assert "prix_valeur" not in columns and columns[-1] == "valeur"
    assert all(row["valeur"] == "2.3" and "prix_valeur" not in row for row in rows)


def test_null_spike_blanks_the_requested_share_the_same_way_every_time():
    first = faults.blank_prices(tables(10), {"fraction": 0.3})["prices.csv"][1]
    second = faults.blank_prices(tables(10), {"fraction": 0.3})["prices.csv"][1]

    blanks = [i for i, row in enumerate(first) if row["prix_valeur"] == ""]
    assert len(blanks) == 3
    assert blanks == [i for i, row in enumerate(second) if row["prix_valeur"] == ""]


def test_duplicates_append_the_whole_day_again():
    columns, rows = faults.duplicate_prices(tables(4), {})["prices.csv"]

    assert len(rows) == 8 and rows[:4] == rows[4:]


def test_unit_drift_writes_thousandths_and_leaves_blanks_alone():
    day = tables(3)
    day["prices.csv"][1][1]["prix_valeur"] = "2.259"
    day["prices.csv"][1][2]["prix_valeur"] = ""

    rows = faults.scale_prices(day, {})["prices.csv"][1]

    assert [row["prix_valeur"] for row in rows] == ["2300", "2259", ""]


def test_freshness_answers_like_a_real_missing_day():
    with pytest.raises(SourceUnavailable, match=r"no archive for 2026-09-13: got \d+ bytes of text/html"):
        faults.fetch_for(DAY, armed("freshness"))(DAY)


def test_source_error_is_an_http_500_on_every_rerun():
    fetch = faults.fetch_for(DAY, armed("source_error"))

    for _ in range(2):
        with pytest.raises(urllib.error.HTTPError, match="HTTP Error 500"):
            fetch(DAY)


def test_source_faults_do_not_tamper_with_files():
    assert faults.tamper_for(DAY, armed("freshness")) is None
    assert faults.tamper_for(DAY, armed("source_error")) is None


def test_nothing_changes_while_no_fault_is_armed():
    assert faults.fetch_for(DAY, None) is fetch_archive
    assert faults.tamper_for(DAY, None) is None


def test_an_armed_fault_only_targets_its_own_day():
    fault = {"name": "null_spike", "date": "2026-09-12", "params": {}}

    assert faults.fetch_for(DAY, fault) is fetch_archive
    assert faults.tamper_for(DAY, fault) is None


def test_an_armed_data_fault_reads_the_clean_backup_and_tampers(tmp_path):
    (tmp_path / "2026-09-13").mkdir()
    (tmp_path / "2026-09-13" / "source.zip").write_bytes(b"clean archive")

    assert faults.fetch_for(DAY, armed("duplicate_rows"), backup_dir=tmp_path)(DAY) == b"clean archive"
    assert len(faults.tamper_for(DAY, armed("duplicate_rows"))(tables(2))["prices.csv"][1]) == 4


def test_a_truncated_delivery_drops_half_the_stations_with_their_prices():
    stations = [{"pdv_id": str(i)} for i in range(10)]
    prices = [{"pdv_id": str(i), "prix_valeur": "2.3"} for i in range(10)]
    day = {"stations.csv": (["pdv_id"], stations), "prices.csv": (["pdv_id", "prix_valeur"], prices)}

    truncated = faults.drop_half_the_stations(day, {})

    kept = {s["pdv_id"] for s in truncated["stations.csv"][1]}
    assert len(kept) == 5
    assert {p["pdv_id"] for p in truncated["prices.csv"][1]} == kept


def mixed_day():
    stations = [{"pdv_id": str(i), "cp": cp} for i, cp in enumerate(["29200", "35000", "75001", "13001"])]
    prices = [{"pdv_id": str(i), "prix_nom": fuel, "prix_valeur": value}
              for i in range(4) for fuel, value in (("Gazole", "2.3"), ("E85", "0.9"), ("SP98", ""))]
    return {"stations.csv": (["pdv_id", "cp"], stations),
            "prices.csv": (["pdv_id", "prix_nom", "prix_valeur"], prices)}


def test_unit_drift_on_one_fuel_leaves_the_other_fuels_alone():
    rows = faults.scale_one_fuel(mixed_day(), {"fuel": "E85"})["prices.csv"][1]

    assert {r["prix_valeur"] for r in rows if r["prix_nom"] == "E85"} == {"900"}
    assert {r["prix_valeur"] for r in rows if r["prix_nom"] == "Gazole"} == {"2.3"}
    assert {r["prix_valeur"] for r in rows if r["prix_nom"] == "SP98"} == {""}


def test_partial_duplicates_send_a_share_of_the_stations_twice_the_same_way_every_time():
    first = faults.duplicate_some_stations(mixed_day(), {"share": 0.5})["prices.csv"][1]
    second = faults.duplicate_some_stations(mixed_day(), {"share": 0.5})["prices.csv"][1]

    duplicated = first[12:]
    assert len(first) == 12 + 6 and first == second
    assert len({r["pdv_id"] for r in duplicated}) == 2  # half of the 4 stations, with all their prices
    assert duplicated == [r for r in first[:12] if r["pdv_id"] in {d["pdv_id"] for d in duplicated}]


def test_zero_prices_replace_a_share_of_the_filled_prices_and_never_a_blank():
    rows = faults.zero_some_prices(mixed_day(), {"share": 0.5})["prices.csv"][1]

    assert sum(r["prix_valeur"] == "0" for r in rows) == 4  # half of the 8 filled prices
    assert sum(r["prix_valeur"] == "" for r in rows) == 4  # the blanks stay blank
    assert rows == faults.zero_some_prices(mixed_day(), {"share": 0.5})["prices.csv"][1]


def test_a_missing_region_drops_its_stations_and_their_prices():
    day = faults.drop_a_region(mixed_day(), {"departments": ["22", "29", "35", "56"]})

    assert [s["cp"] for s in day["stations.csv"][1]] == ["75001", "13001"]
    assert {p["pdv_id"] for p in day["prices.csv"][1]} == {"2", "3"}


def test_a_price_rise_moves_every_price_and_leaves_blanks_alone():
    rows = faults.raise_all_prices(mixed_day(), {"factor": 1.08})["prices.csv"][1]

    assert {r["prix_valeur"] for r in rows} == {"2.484", "0.972", ""}


def two_faults(*names):
    return {"faults": [{"name": n, "params": {}} for n in names], "date": "2026-09-13"}


def test_two_data_faults_are_applied_one_after_the_other():
    rows = faults.tamper_for(DAY, two_faults("unit_drift", "duplicate_rows"))(tables(2))["prices.csv"][1]

    assert [row["prix_valeur"] for row in rows] == ["2300"] * 4


def test_a_source_fault_wins_over_a_data_fault(tmp_path):
    fetch = faults.fetch_for(DAY, two_faults("unit_drift", "source_error"), backup_dir=tmp_path)

    with pytest.raises(urllib.error.HTTPError):
        fetch(DAY)


def test_a_healthy_delivery_changes_nothing_and_reads_the_clean_archive(tmp_path):
    (tmp_path / "2026-09-13").mkdir()
    (tmp_path / "2026-09-13" / "source.zip").write_bytes(b"clean archive")

    assert faults.tamper_for(DAY, armed("healthy"))(tables(3)) == tables(3)
    assert faults.fetch_for(DAY, armed("healthy"), backup_dir=tmp_path)(DAY) == b"clean archive"


def test_the_ingestion_writes_what_the_fault_delivers(tmp_path):
    summary = ingest_day(DAY, raw_dir=tmp_path, fetch=lambda day: archive(),
                         tamper=faults.tamper_for(DAY, armed("schema_drift")))

    header = (tmp_path / "2026-09-13" / "prices.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == "pdv_id,prix_id,prix_nom,prix_maj,valeur"
    assert summary["status"] == "ingested"
