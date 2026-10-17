"""quota.warnings: English for agents, the app's Chinese wording with lang="zh"."""

from litloop import quota


def _fake_summary(monkeypatch):
    monkeypatch.setattr(quota, "summary", lambda: {
        "scopus": {"remaining": 414, "limit": 10000, "resets_at": "2026-10-22 16:45", "throttled_today": 0},
        "openalex": {"remaining": None, "limit": None, "resets_at": None, "throttled_today": 110},
        "crossref": {"remaining": None, "limit": None, "resets_at": None, "throttled_today": 1},
    })


def test_english_for_agents(monkeypatch):
    _fake_summary(monkeypatch)
    assert quota.warnings() == [
        "scopus: only 414 of 10000 requests left, resets at 2026-10-22 16:45",
        "openalex: throttled 110 times today; requests are slowed down",
    ]


def test_chinese_for_the_app(monkeypatch):
    _fake_summary(monkeypatch)
    assert quota.warnings("zh") == [
        "Scopus 剩余 414 / 10,000 次请求，2026-10-22 16:45 重置",
        "OpenAlex 今日被限流 110 次，已放慢请求",
    ]
