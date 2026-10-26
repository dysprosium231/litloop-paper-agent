"""Tests for school database and lookup helpers in litloop.institutional.schools."""

from __future__ import annotations

import pytest

from litloop.institutional.schools import (
    SchoolEntry,
    _DEFAULT_KEY,
    _parse_entry,
    get_school,
    list_schools,
    search_schools,
)


def test_parse_entry_valid_full():
    entry = _parse_entry(
        name="测试大学",
        province="北京",
        info={
            "host": "https://vpn.example.edu.cn",
            "crypto_key": "custom_key_12345",
            "crypto_iv": "custom_iv_123456",
            "type": "easyconnect",
            "gateway": "vpn.example.edu.cn",
        },
    )
    assert entry is not None
    assert entry.name == "测试大学"
    assert entry.province == "北京"
    assert entry.host == "https://vpn.example.edu.cn"
    assert entry.key == b"custom_key_12345"
    assert entry.iv == b"custom_iv_123456"
    assert entry.school_type == "easyconnect"
    assert entry.gateway == "vpn.example.edu.cn"


def test_parse_entry_defaults():
    entry = _parse_entry(
        name="默认大学",
        province="上海",
        info={"host": "webvpn.example.edu.cn"},
    )
    assert entry is not None
    assert entry.host == "https://webvpn.example.edu.cn"
    assert entry.key == _DEFAULT_KEY
    assert entry.iv == _DEFAULT_KEY
    assert entry.school_type == "webvpn"
    assert entry.gateway == ""


def test_parse_entry_iv_fallback_to_key():
    entry = _parse_entry(
        name="密钥大学",
        province="广东",
        info={"host": "https://vpn.example.edu.cn", "crypto_key": "mysecretkey12345"},
    )
    assert entry is not None
    assert entry.key == b"mysecretkey12345"
    assert entry.iv == b"mysecretkey12345"


def test_parse_entry_empty_host_returns_none():
    assert _parse_entry("无主机大学", "天津", {}) is None
    assert _parse_entry("空白主机大学", "天津", {"host": "   "}) is None


def test_list_schools_loads_and_caches():
    schools = list_schools()
    assert isinstance(schools, list)
    assert len(schools) > 0
    assert all(isinstance(s, SchoolEntry) for s in schools)
    assert all(s.host.startswith("http") for s in schools)

    # Calling list_schools again should hit memory cache
    cached = list_schools()
    assert cached is schools


def test_search_schools_by_chinese_name():
    results = search_schools("清华")
    assert len(results) > 0
    assert any("清华" in s.name for s in results)


def test_search_schools_by_code_and_case_insensitive():
    results_upper = search_schools("USTC")
    results_lower = search_schools("ustc")
    assert len(results_upper) > 0
    assert len(results_upper) == len(results_lower)
    assert any("ustc" in s.host.lower() for s in results_upper)


def test_search_schools_by_province():
    results = search_schools("安徽")
    assert len(results) > 0
    assert all("安徽" in s.province or "安徽" in s.name for s in results)


def test_search_schools_no_match():
    results = search_schools("NonExistentUniversity987654")
    assert results == []


def test_get_school_exact_and_partial():
    exact = get_school("中国科学技术大学")
    assert exact.name == "中国科学技术大学"

    partial = get_school("中国科学技术")
    assert partial.name == "中国科学技术大学"


def test_get_school_reverse_match():
    # When user enters full campus or affiliated name containing the school
    matched = get_school("中国科学技术大学先进技术研究院")
    assert matched.name == "中国科学技术大学"


def test_get_school_unknown_raises():
    with pytest.raises(ValueError, match="School not found"):
        get_school("完全不存在的虚拟科技大学999")
