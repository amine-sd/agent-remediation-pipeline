import io
import zipfile
from datetime import date
from email.message import Message

import pytest

from pipeline import ingest
from pipeline.ingest import SourceUnavailable, fetch_archive, ingest_day, parse_archive

DAY = date(2026, 9, 10)

SAMPLE_XML = """<?xml version="1.0" encoding="ISO-8859-1" standalone="yes"?>
<pdv_liste>
  <pdv id="1000001" latitude="4620100" longitude="519800" cp="01000" pop="R">
    <adresse>596 AVENUE DE TREVOUX</adresse>
    <ville>SAINT-DENIS-LÈS-BOURG</ville>
    <horaires automate-24-24="1"></horaires>
    <services><service>DAB</service><service>Wifi</service></services>
    <prix nom="Gazole" id="1" maj="2026-09-10T18:44:00" valeur="2.259"/>
    <prix nom="E85" id="3" maj="2026-09-10T18:44:00" valeur="0.869"/>
  </pdv>
  <pdv id="1000002" latitude="" longitude="" cp="20000" pop="A">
    <adresse>RUE DU PORT</adresse>
    <ville>AJACCIO</ville>
    <services/>
  </pdv>
</pdv_liste>
""".encode("iso-8859-1")


def make_zip(xml: bytes = SAMPLE_XML) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("PrixCarburants_quotidien_20260910.xml", xml)
    return buffer.getvalue()


def read_day(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir()}


def test_parse_archive_flattens_stations_and_prices():
    stations, prices = parse_archive(make_zip())

    assert [s["pdv_id"] for s in stations] == ["1000001", "1000002"]
    assert stations[0]["ville"] == "SAINT-DENIS-LÈS-BOURG"  # decoded from ISO-8859-1
    assert stations[0]["services"] == "DAB|Wifi"
    assert stations[0]["automate_24_24"] == "1"
    assert stations[1]["latitude"] == ""
    assert [(p["pdv_id"], p["prix_nom"], p["prix_valeur"]) for p in prices] == [
        ("1000001", "Gazole", "2.259"),
        ("1000001", "E85", "0.869"),
    ]


def test_ingest_day_writes_the_day_folder(tmp_path):
    summary = ingest_day(DAY, raw_dir=tmp_path, fetch=lambda d: make_zip())

    assert summary == {"date": "2026-09-10", "status": "ingested", "stations": 2, "prices": 2}
    assert sorted(read_day(tmp_path / "2026-09-10")) == ["prices.csv", "source.zip", "stations.csv"]
    assert not any((tmp_path / ".tmp").iterdir())


def test_second_run_downloads_and_writes_nothing(tmp_path):
    calls = []

    def fetch(day):
        calls.append(day)
        return make_zip()

    ingest_day(DAY, raw_dir=tmp_path, fetch=fetch)
    before = read_day(tmp_path / "2026-09-10")
    summary = ingest_day(DAY, raw_dir=tmp_path, fetch=fetch)

    assert summary["status"] == "skipped"
    assert calls == [DAY]
    assert read_day(tmp_path / "2026-09-10") == before


def test_failed_download_leaves_no_day_folder(tmp_path):
    def fetch(day):
        raise SourceUnavailable("no archive")

    with pytest.raises(SourceUnavailable):
        ingest_day(DAY, raw_dir=tmp_path, fetch=fetch)
    assert not (tmp_path / "2026-09-10").exists()


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_type: str):
        super().__init__(payload)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


def test_html_page_with_status_200_is_not_an_archive(monkeypatch):
    html = FakeResponse(b"<!DOCTYPE html><html>...</html>", "text/html")
    monkeypatch.setattr(ingest.urllib.request, "urlopen", lambda request, timeout: html)

    with pytest.raises(SourceUnavailable, match="text/html"):
        fetch_archive(date(2026, 9, 14))


def test_zip_answer_is_returned(monkeypatch):
    archive = make_zip()
    response = FakeResponse(archive, "application/zip")
    monkeypatch.setattr(ingest.urllib.request, "urlopen", lambda request, timeout: response)

    assert fetch_archive(DAY) == archive
