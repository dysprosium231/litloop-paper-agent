"""Phase 1 does not open the publisher site for publisher_site_skip_prefixes."""

from __future__ import annotations

import litloop.sources as src


def _labels(doi, cfg):
    return [label for _, label in src._build_free_sources(doi, cfg)]


def test_tf_books_skip_the_site():
    cfg = {"publisher_site_skip_prefixes": ["10.1201", "10.4324"], "scihub_enabled": False}
    assert not any("Browser" in x for x in _labels("10.1201/9781003075837", cfg))
    assert not any("Browser" in x for x in _labels("10.4324/9780429000000", cfg))


def test_other_publishers_keep_it():
    cfg = {"publisher_site_skip_prefixes": ["10.1201"], "scihub_enabled": False}
    assert any("Browser" in x for x in _labels("10.1016/j.jcis.2012.08.004", cfg))


def test_empty_setting_keeps_it():
    cfg = {"publisher_site_skip_prefixes": [], "scihub_enabled": False}
    assert any("Browser" in x for x in _labels("10.1201/9781003075837", cfg))


class _Page:
    def __init__(self, url, title):
        self.url, self._t = url, title

    def title(self):
        return self._t


def test_browser_error_page():
    from litloop.publishers.strategies._core import _is_browser_error_page
    assert _is_browser_error_page(_Page("chrome-error://chromewebdata/", ""))
    assert _is_browser_error_page(_Page("https://papers.ssrn.com/x", "无法访问此网站"))
    assert not _is_browser_error_page(_Page("https://papers.ssrn.com/x", "Just a moment..."))
    assert not _is_browser_error_page(_Page("https://www.sciencedirect.com/x", "Article title"))
