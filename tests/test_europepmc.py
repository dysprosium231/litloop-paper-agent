"""Europe PMC and PMC lookups, with the network faked."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from litloop.sources import europepmc


def test_pdf_links_are_picked_from_the_fulltext_list():
    payload = {
        "resultList": {
            "result": [
                {
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {"url": "https://example.org/a", "documentStyle": "html"},
                            {"url": "https://example.org/b.pdf", "documentStyle": "pdf"},
                            {"url": "https://example.org/c", "documentStyle": ""},
                        ]
                    }
                }
            ]
        }
    }
    assert europepmc.extract_europepmc_pdf_candidates(payload) == ["https://example.org/b.pdf"]


def test_one_url_given_as_an_object_still_works():
    payload = {"resultList": {"result": [{"fullTextUrlList": {"fullTextUrl": {"url": "https://e.org/x.pdf"}}}]}}
    assert europepmc.extract_europepmc_pdf_candidates(payload) == ["https://e.org/x.pdf"]


def test_a_malformed_payload_yields_nothing():
    assert europepmc.extract_europepmc_pdf_candidates({}) == []
    assert europepmc.extract_europepmc_pdf_candidates({"resultList": {"result": "oops"}}) == []
    assert europepmc.extract_europepmc_pdf_candidates({"resultList": {"result": ["oops", 3]}}) == []


def test_trying_europe_pmc_downloads_the_first_candidate(monkeypatch, tmp_path):
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI%3A10.1/x&format=json&pageSize=5"
    asked: list[str] = []

    def fake_json(asked_url, config, headers=None):
        asked.append(asked_url)
        return {"resultList": {"result": [{"fullTextUrlList": {"fullTextUrl": [
            {"url": "https://e.org/paper.pdf", "documentStyle": "pdf"}]}}]}}

    downloaded: list[str] = []

    def fake_download(pdf_url, output_path, config, source):
        downloaded.append(pdf_url)
        return {"file": str(output_path), "source": source, "source_name": source}

    monkeypatch.setattr(europepmc, "fetch_json", fake_json)
    monkeypatch.setattr(europepmc, "download_pdf", fake_download)
    monkeypatch.setattr(europepmc, "polite_delay", lambda config: None)

    result = europepmc.try_europepmc("10.1/x", Path(tmp_path) / "out.pdf", {})
    assert asked == [url]
    assert downloaded == ["https://e.org/paper.pdf"]
    assert result["doi"] == "10.1/x" and result["identifier"] == "10.1/x"


def test_europe_pmc_gives_up_when_nothing_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(europepmc, "fetch_json", lambda *a, **k: {"resultList": {"result": []}})
    monkeypatch.setattr(europepmc, "polite_delay", lambda config: None)
    assert europepmc.try_europepmc("10.1/x", Path(tmp_path) / "out.pdf", {}) is None


def test_pmc_asks_the_id_converter_and_then_the_article(monkeypatch, tmp_path):
    asked: list[str] = []

    def fake_json(asked_url, config, headers=None):
        asked.append(asked_url)
        return {"records": [{"pmcid": "PMC1234"}]}

    downloaded: list[str] = []

    def fake_download(pdf_url, output_path, config, source):
        downloaded.append(pdf_url)
        return {"file": str(output_path), "source": source}

    monkeypatch.setattr(europepmc, "fetch_json", fake_json)
    monkeypatch.setattr(europepmc, "download_pdf", fake_download)

    result = europepmc.try_pmc("10.1/x", Path(tmp_path) / "out.pdf", {"email": "me@example.org"})
    assert downloaded == ["https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234/pdf/"]
    assert "idtype=doi" in asked[0] and "email=me%40example.org" in asked[0]
    assert result["doi"] == "10.1/x"


def test_pmc_leaves_out_a_placeholder_email(monkeypatch, tmp_path):
    asked: list[str] = []

    def fake_json(asked_url, config, headers=None):
        asked.append(asked_url)
        return {"records": []}

    monkeypatch.setattr(europepmc, "fetch_json", fake_json)
    assert europepmc.try_pmc("10.1/x", Path(tmp_path) / "out.pdf", {"email": "me@example.invalid"}) is None
    assert "email=" not in asked[0]


def test_pmc_treats_a_lookup_error_as_no_pdf(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("网断了")

    monkeypatch.setattr(europepmc, "fetch_json", boom)
    assert europepmc.try_pmc("10.1/x", Path(tmp_path) / "out.pdf", {}) is None


def test_the_query_is_escaped(monkeypatch, tmp_path):
    asked: list[str] = []

    def fake_json(asked_url, config, headers=None):
        asked.append(asked_url)
        return {}

    monkeypatch.setattr(europepmc, "fetch_json", fake_json)
    europepmc.try_europepmc("10.1016/j.ceramint.2021.06.118", Path(tmp_path) / "o.pdf", {})
    assert asked[0].endswith(urllib.parse.quote("DOI:10.1016/j.ceramint.2021.06.118") + "&format=json&pageSize=5")
