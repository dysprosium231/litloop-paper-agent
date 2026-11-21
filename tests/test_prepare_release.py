"""Tests for scripts/prepare_release.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import prepare_release  # noqa: E402


def _make_fake_repo(root: Path, versions: dict[str, str]) -> Path:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "litloop"\nversion = "{versions["pyproject"]}"\n',
        encoding="utf-8",
        newline="\n",
    )
    init_file = root / "src" / "litloop" / "__init__.py"
    init_file.parent.mkdir(parents=True, exist_ok=True)
    init_file.write_text(
        f'__version__ = "{versions["init"]}"\n',
        encoding="utf-8",
        newline="\n",
    )
    tauri_file = root / "app" / "src-tauri" / "tauri.conf.json"
    tauri_file.parent.mkdir(parents=True, exist_ok=True)
    tauri_file.write_text(
        json.dumps({"productName": "LitLoop", "version": versions["tauri"]}),
        encoding="utf-8",
        newline="\n",
    )
    (root / "server.json").write_text(
        json.dumps({"name": "io.github.dysprosium231/litloop", "version": versions["server"]}),
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_check_versions_passes_when_all_four_match(tmp_path: Path) -> None:
    repo = _make_fake_repo(
        tmp_path,
        {"pyproject": "1.18.3", "init": "1.18.3", "tauri": "1.18.3", "server": "1.18.3"},
    )
    version, problems = prepare_release.check_versions(repo)
    assert version == "1.18.3"
    assert problems == []


def test_check_versions_fails_on_mismatch(tmp_path: Path) -> None:
    repo = _make_fake_repo(
        tmp_path,
        {"pyproject": "1.18.3", "init": "1.18.3", "tauri": "1.18.2", "server": "1.18.3"},
    )
    _, problems = prepare_release.check_versions(repo)
    assert len(problems) == 1
    assert "version mismatch" in problems[0]
    assert "app/src-tauri/tauri.conf.json=1.18.2" in problems[0]

    rc = prepare_release.run_checks(skip_smoke=True, skip_frontend=True, root=repo)
    assert rc == 1


def test_real_repo_four_versions_agree() -> None:
    version, problems = prepare_release.check_versions()
    assert version
    assert problems == []


def test_write_updater_manifest_cli_format(tmp_path: Path) -> None:
    installer = tmp_path / "LitLoop_1.18.3_x64-setup.exe"
    installer.write_bytes(b"fake-exe")
    sig = tmp_path / "LitLoop_1.18.3_x64-setup.exe.sig"
    sig.write_text("dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZQ==\n", encoding="utf-8", newline="\n")
    out = tmp_path / "latest.json"

    rc = prepare_release.main(
        [
            "--write-updater-manifest",
            "--version",
            "1.18.3",
            "--installer",
            str(installer),
            "--signature",
            str(sig),
            "--out",
            str(out),
            "--notes",
            "Release 1.18.3",
        ]
    )
    assert rc == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw

    data = json.loads(raw.decode("utf-8"))
    assert data["version"] == "1.18.3"
    assert data["notes"] == "Release 1.18.3"
    assert data["pub_date"].endswith("Z")
    win = data["platforms"]["windows-x86_64"]
    assert win["signature"] == "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZQ=="
    assert win["url"] == (
        "https://github.com/dysprosium231/litloop-paper-agent/releases/download/"
        "v1.18.3/LitLoop_1.18.3_x64-setup.exe"
    )


def test_write_updater_manifest_requires_all_flags(tmp_path: Path) -> None:
    rc = prepare_release.main(["--write-updater-manifest", "--version", "1.18.3"])
    assert rc == 2
