"""The diagnostic bundle: what it collects, and what it must never carry."""

from __future__ import annotations

import json
import zipfile

from litloop import diagnostics, log


def test_redact_masks_the_keys_but_not_the_paths():
    masked = diagnostics.redact({
        "openalex_api_key": "abc123",
        "elsevier_api_key": "",
        "output_dir": "D:/papers",
        "nested": {"builtin_agent_api_key": "sk-secret", "count": 3},
        "cookies": "sessionid=1",
    })
    assert masked["openalex_api_key"] == "***"
    assert masked["elsevier_api_key"] == ""          # nothing to hide
    assert masked["output_dir"] == "D:/papers"
    assert masked["nested"] == {"builtin_agent_api_key": "***", "count": 3}
    assert masked["cookies"] == "***"


def test_collect_has_the_sections_a_bug_report_needs():
    from litloop import __version__
    data = diagnostics.collect({"openalex_api_key": "abc", "output_dir": "D:/papers"})
    assert data["app"]["version"] == __version__
    assert data["app"]["python"]
    assert data["system"]["os"]
    assert data["settings"]["openalex_api_key"] == "***"
    assert data["settings"]["output_dir"] == "D:/papers"
    assert isinstance(data["log"], list)
    assert set(data["paths"]) >= {"path", "papers", "exports"}


def test_recent_log_lines_show_up_in_the_bundle(tmp_path):
    log.get_logger().info("diagnostics test: 一条日志")
    assert any("diagnostics test" in line for line in log.recent(50))


def test_write_bundle_leaves_a_readable_zip_without_secrets(tmp_path):
    config = {"openalex_api_key": "sk-should-not-appear", "output_dir": str(tmp_path)}
    path = diagnostics.write_bundle(tmp_path, config=config)
    assert path.exists() and path.suffix == ".zip"
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"diagnostics.json", "log.txt", "README.txt"}
        payload = json.loads(archive.read("diagnostics.json"))
        assert payload["settings"]["openalex_api_key"] == "***"
        assert "sk-should-not-appear" not in archive.read("diagnostics.json").decode("utf-8")
        assert "PDF" in archive.read("README.txt").decode("utf-8")


def test_write_bundle_accepts_a_file_name_too(tmp_path):
    target = tmp_path / "custom.zip"
    assert diagnostics.write_bundle(target) == target
    assert target.is_file()
