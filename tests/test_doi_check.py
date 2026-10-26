"""A DOI that doi.org does not know fails at once, before any source is asked."""

from __future__ import annotations

import pytest
import requests

from litloop import error_text, sources
from litloop.download import doi_check


class _Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(doi_check, "_KNOWN", {})


def test_answers_from_doi_org_are_kept(monkeypatch):
    asked = []

    def get(url, **kw):
        asked.append(url)
        return _Resp(404, {"responseCode": 100}) if "nothere" in url else _Resp(200, {"responseCode": 1})

    monkeypatch.setattr(requests, "get", get)
    assert doi_check._real_registered("10.1016/j.a.2020.1") is True
    assert doi_check._real_registered("10.1016/J.NOTHERE.1") is False
    assert doi_check._real_registered("10.1016/j.nothere.1") is False
    assert len(asked) == 2  # the second ask of the same DOI is answered from memory


@pytest.mark.parametrize("answer", [
    requests.ConnectionError("offline"),
    _Resp(500, {"responseCode": 1}),
    _Resp(200, ValueError("not json")),
])
def test_no_clear_answer_leaves_it_open(monkeypatch, answer):
    def get(url, **kw):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests, "get", get)
    assert doi_check._real_registered("10.1016/j.a.2020.1") is None
    assert doi_check._real_registered("arXiv:2401.00001") is None


def test_download_of_an_unregistered_doi_stops_before_the_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(doi_check, "registered", lambda doi, timeout=6.0: False)
    monkeypatch.setattr(sources, "_download_impl", lambda *a, **k: pytest.fail("sources were asked"))
    r = sources.download("10.1016/j.typo.2020.1", tmp_path)
    assert not r["success"] and r["error_type"] == "doi_not_found"
    assert error_text.reason(r.get("error") or r.get("reason")) == "这个 DOI 不存在"
