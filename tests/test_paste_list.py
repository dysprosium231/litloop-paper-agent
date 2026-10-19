"""A pasted list of DOIs, links and arXiv ids becomes a download list."""

from __future__ import annotations

from fastapi.testclient import TestClient

from litloop import app_server
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
    from litloop import download_jobs
    monkeypatch.setattr(download_jobs, "downloaded_dois", lambda: ["10.1016/j.jallcom.2025.179147"])
    c = TestClient(app_server.create_app("t"))
    r = c.post("/api/downloads/parse", json={"text": PASTED}, headers={"X-LitLoop-Token": "t"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["papers"]) == 8 and body["unrecognized_count"] == 1
    assert body["downloaded"] == ["10.1016/j.jallcom.2025.179147"]
