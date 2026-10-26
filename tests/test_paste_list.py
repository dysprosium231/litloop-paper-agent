"""A pasted list of DOIs, links and arXiv ids becomes a download list."""

from __future__ import annotations

from fastapi.testclient import TestClient

from litloop.desktop import server as app_server
from litloop.identifiers import extract_identifiers

PASTED = """\
10.1016/j.jallcom.2025.179147
https://doi.org/10.1007/978-981-96-2944-2_32
doi: 10.1038/s41586-024-07000-1.
See https://www.sciencedirect.com/science/article/pii/S0272884224012345 and 10.1016/j.ceramint.2024.08.002, 10.1016/J.CERAMINT.2024.08.002
https://onlinelibrary.wiley.com/doi/pdf/10.1002/adma.202400001
https://arxiv.org/abs/2401.01234v2
arXiv:2312.00001
2402.05678
10.48550/arXiv.2401.01234
Smith et al. 2020, Nature (no identifier)

"""


def test_every_kind_of_identifier_is_found_once():
    dois, rest = extract_identifiers(PASTED)
    assert dois == [
        "10.1016/j.jallcom.2025.179147",
        "10.1007/978-981-96-2944-2_32",
        "10.1038/s41586-024-07000-1",
        "10.1016/j.ceramint.2024.08.002",
        "10.1002/adma.202400001",
        "10.48550/arxiv.2401.01234",
        "10.48550/arxiv.2312.00001",
        "10.48550/arxiv.2402.05678",
    ]
    assert rest == ["Smith et al. 2020, Nature (no identifier)"]


def test_endpoint_reports_what_it_found_and_what_was_downloaded(monkeypatch):
    from litloop.desktop import download_jobs
    monkeypatch.setattr(download_jobs, "local_files", lambda: {"10.1016/j.jallcom.2025.179147": "a.pdf"})
    c = TestClient(app_server.create_app("t"))
    r = c.post("/api/downloads/parse", json={"text": PASTED}, headers={"X-LitLoop-Token": "t"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["papers"]) == 8 and body["unrecognized_count"] == 1
    assert body["downloaded"] == ["10.1016/j.jallcom.2025.179147"]


def test_dois_copied_from_pdfs_keep_their_hyphens():
    pasted = "doi:10.1007/978\u2010981\u201096\u20102944\u20102_32\n10.1016/j.ceramint.2024.08.002\u00a0(2024)"
    dois, rest = extract_identifiers(pasted)
    assert dois == ["10.1007/978-981-96-2944-2_32", "10.1016/j.ceramint.2024.08.002"] and rest == []


class _Resp:
    def __init__(self, items):
        self.status_code, self._items, self.headers = 200, items, {}

    def json(self):
        return {"message": {"items": self._items}}


def test_a_reference_line_finds_its_paper_and_a_near_miss_does_not(monkeypatch):
    import requests
    from litloop.search import title_to_doi as resolver
    ref = ("Wu, C., Lv, G. (2025). Preparation and Performance Analysis of SiCp/Al Composite Materials "
           "by Non pressure Infiltration Method. In Proceedings of MMTME 2024, pp. 338-344. Springer.")
    good = {"DOI": "10.1007/978-981-96-2944-2_32", "issued": {"date-parts": [[2025]]},
            "title": ["Preparation and Performance Analysis of SiCp/Al Composite Materials by Non pressure Infiltration Method"]}
    other = {"DOI": "10.1016/x", "title": ["Thermal conductivity of diamond reinforced copper matrix composites"]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp([other, good]))
    assert resolver.resolve_reference(ref, {}) == {"doi": "10.1007/978-981-96-2944-2_32", "title": good["title"][0], "year": 2025}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp([other]))
    assert resolver.resolve_reference(ref, {}) is None


def test_resolve_endpoint_marks_what_was_downloaded(monkeypatch):
    from litloop.desktop import download_jobs
    from litloop.search import title_to_doi as resolver
    monkeypatch.setattr(resolver, "resolve_reference",
                        lambda line, config, timeout=15.0: {"doi": "10.1/a", "title": "A paper", "year": 2020} if "A paper" in line else None)
    monkeypatch.setattr(download_jobs, "local_files", lambda: {"10.1/a": "a.pdf"})
    c = TestClient(app_server.create_app("t"))
    r = c.post("/api/downloads/resolve", json={"lines": ["Smith (2020). A paper. J. 1-2.", "nothing here at all, really"]},
               headers={"X-LitLoop-Token": "t"})
    rows = r.json()["results"]
    assert rows[0]["doi"] == "10.1/a" and rows[0]["downloaded"] is True
    assert rows[1] == {"line": "nothing here at all, really"}
