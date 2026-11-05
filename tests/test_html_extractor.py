"""The HTML router picks the publisher adapter by URL, generic otherwise."""

from __future__ import annotations

import pytest

from litloop.extractors import html_extractor
from litloop.extractors.publisher_adapters import (
    acs,
    elsevier,
    generic,
    nature,
    rsc,
    tandfonline,
    wiley,
)


@pytest.mark.parametrize(
    "url, adapter",
    [
        ("https://www.nature.com/articles/s41598-024-67653-y", nature),
        ("https://link.springer.com/article/10.1007/s10853-021-00001-1", nature),
        ("https://www.sciencedirect.com/science/article/pii/S0000000000000000", elsevier),
        ("https://onlinelibrary.wiley.com/doi/10.1002/adma.202000001", wiley),
        ("https://pubs.acs.org/doi/10.1021/acsami.0c00001", acs),
        ("https://pubs.rsc.org/en/content/articlelanding/2020/ta/d0ta00001a", rsc),
        ("https://www.tandfonline.com/doi/full/10.1080/00000000.2020.0000001", tandfonline),
    ],
)
def test_the_router_picks_the_adapter_for_the_domain(monkeypatch, url, adapter):
    called = {}

    def fake_extract(html, url=""):
        called["adapter"] = adapter.__name__
        return {"title": adapter.__name__}

    monkeypatch.setattr(adapter, "extract", fake_extract)
    assert html_extractor.extract("<html></html>", url) == {"title": adapter.__name__}
    assert called["adapter"] == adapter.__name__


def test_an_unknown_domain_falls_back_to_generic(monkeypatch):
    seen = []
    monkeypatch.setattr(generic, "extract", lambda html, url="": seen.append(url) or {"title": "generic"})
    assert html_extractor.extract("<html></html>", "https://example.org/paper") == {"title": "generic"}
    assert seen == ["https://example.org/paper"]


def test_no_url_still_works(monkeypatch):
    monkeypatch.setattr(generic, "extract", lambda html, url="": {"title": "generic"})
    assert html_extractor.extract("<html></html>")["title"] == "generic"


def test_the_first_matching_adapter_wins(monkeypatch):
    """Order matters: Nature comes before Elsevier, so a shared URL goes to Nature.

    Nothing in the list overlaps today; this pins the behaviour so a future
    broad pattern (say ``.com``) cannot silently shadow the others.
    """
    order = [a.__name__ for a in html_extractor._ADAPTERS]
    assert order.index(nature.__name__) < order.index(elsevier.__name__)
    assert generic not in html_extractor._ADAPTERS


def test_the_url_match_is_case_insensitive():
    for adapter in (nature, elsevier, wiley, acs, rsc, tandfonline):
        probe = {
            nature: "HTTPS://WWW.NATURE.COM/ARTICLES/1",
            elsevier: "https://www.ScienceDirect.com/x",
            wiley: "https://ONLINELIBRARY.WILEY.COM/doi/1",
            acs: "https://PUBS.ACS.ORG/doi/1",
            rsc: "https://PUBS.RSC.ORG/x",
            tandfonline: "https://WWW.TANDFONLINE.COM/x",
        }[adapter]
        assert adapter.can_handle(probe), adapter.__name__
