#!/usr/bin/env python3
"""Release preparation and updater manifest generator. Standard library only.

Usage:
    python scripts/prepare_release.py [--check] [--skip-smoke] [--skip-frontend]
    python scripts/prepare_release.py --write-updater-manifest \
        --version X.Y.Z --installer <path> --signature <path> --out <path>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO_SLUG = "dysprosium231/litloop-paper-agent"


def read_four_versions(root: Path = ROOT) -> list[tuple[str, str]]:
    """Read versions from the 4 core release files."""
    sources: list[tuple[str, str]] = []

    pyproject_path = root / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        pyproject_data = tomllib.load(fh)
    sources.append(("pyproject.toml", str(pyproject_data.get("project", {}).get("version", ""))))

    init_path = root / "src" / "litloop" / "__init__.py"
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_path.read_text(encoding="utf-8"))
    sources.append(("src/litloop/__init__.py", m.group(1) if m else ""))

    tauri_path = root / "app" / "src-tauri" / "tauri.conf.json"
    tauri_data = json.loads(tauri_path.read_text(encoding="utf-8"))
    sources.append(("app/src-tauri/tauri.conf.json", str(tauri_data.get("version", ""))))

    server_path = root / "server.json"
    server_data = json.loads(server_path.read_text(encoding="utf-8"))
    sources.append(("server.json", str(server_data.get("version", ""))))

    return sources


def check_versions(root: Path = ROOT) -> tuple[str, list[str]]:
    """Verify the 4 release files share the exact same non-empty version."""
    sources = read_four_versions(root)
    problems: list[str] = []
    versions = {ver for _, ver in sources}
    if "" in versions:
        missing = [name for name, ver in sources if not ver]
        problems.append(f"missing version in: {', '.join(missing)}")
    if len(versions) > 1:
        detail = ", ".join(f"{name}={ver or '<empty>'}" for name, ver in sources)
        problems.append(f"version mismatch: {detail}")
    resolved = next((ver for _, ver in sources if ver), "")
    return resolved, problems


def build_updater_manifest(
    *,
    version: str,
    installer_path: Path,
    signature_path: Path,
    notes: str | None = None,
    url: str | None = None,
    pub_date: str | None = None,
    repo_slug: str = DEFAULT_REPO_SLUG,
) -> dict[str, Any]:
    """Build a Tauri v2 updater latest.json payload for windows-x86_64."""
    clean_version = version.lstrip("v").strip()
    if not clean_version:
        raise ValueError("version must not be empty")

    sig_text = signature_path.read_text(encoding="utf-8").strip()
    if not sig_text:
        raise ValueError(f"signature file is empty: {signature_path}")

    installer_name = installer_path.name
    download_url = url or (
        f"https://github.com/{repo_slug}/releases/download/v{clean_version}/{installer_name}"
    )
    timestamp = pub_date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "version": clean_version,
        "notes": notes if notes is not None else f"LitLoop v{clean_version}",
        "pub_date": timestamp,
        "platforms": {
            "windows-x86_64": {
                "signature": sig_text,
                "url": download_url,
            }
        },
    }


def write_updater_manifest(
    *,
    version: str,
    installer_path: Path,
    signature_path: Path,
    out_path: Path,
    notes: str | None = None,
    url: str | None = None,
    pub_date: str | None = None,
) -> Path:
    """Write a Tauri v2 updater latest.json file using LF line endings."""
    payload = build_updater_manifest(
        version=version,
        installer_path=installer_path,
        signature_path=signature_path,
        notes=notes,
        url=url,
        pub_date=pub_date,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


def _run_step(label: str, cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"[prepare_release] {label}: {' '.join(cmd)}", flush=True)
    done = subprocess.run(cmd, cwd=str(cwd))
    if done.returncode != 0:
        raise SystemExit(done.returncode)


def run_checks(*, skip_smoke: bool = False, skip_frontend: bool = False, root: Path = ROOT) -> int:
    """Run all release readiness checks."""
    version, problems = check_versions(root)
    if problems:
        for problem in problems:
            print(f"[prepare_release] FAIL: {problem}", file=sys.stderr)
        return 1
    print(f"[prepare_release] versions match across 4 core files: {version}", flush=True)

    _run_step(
        "release consistency",
        [sys.executable, str(root / "scripts" / "check_release_consistency.py")],
        cwd=root,
    )
    _run_step(
        "import smoke",
        [sys.executable, str(root / "scripts" / "import_smoke.py")],
        cwd=root,
    )

    if not skip_smoke:
        _run_step(
            "app smoke",
            [sys.executable, str(root / "scripts" / "smoke_app.py")],
            cwd=root,
        )

    if not skip_frontend:
        npm = shutil.which("npm") or "npm"
        app_dir = root / "app"
        _run_step("frontend build", [npm, "run", "build"], cwd=app_dir)
        _run_step("frontend lint:copy", [npm, "run", "lint:copy"], cwd=app_dir)

    print("[prepare_release] all checks passed", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and verify a LitLoop release.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run release readiness checks (default when --write-updater-manifest is not set).",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip scripts/smoke_app.py during --check.",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip npm run build and npm run lint:copy in app/ during --check.",
    )
    parser.add_argument(
        "--write-updater-manifest",
        action="store_true",
        help="Write a Tauri v2 updater latest.json manifest.",
    )
    parser.add_argument("--version", help="Release version (e.g. 1.18.3).")
    parser.add_argument("--installer", help="Path to the Windows NSIS installer executable.")
    parser.add_argument("--signature", help="Path to the .sig signature file.")
    parser.add_argument("--out", help="Output path for latest.json.")
    parser.add_argument("--notes", help="Optional release notes for latest.json.")
    parser.add_argument("--url", help="Optional override download URL for latest.json.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_updater_manifest:
        missing = [
            flag
            for flag, val in (
                ("--version", args.version),
                ("--installer", args.installer),
                ("--signature", args.signature),
                ("--out", args.out),
            )
            if not val
        ]
        if missing:
            print(
                f"[prepare_release] --write-updater-manifest requires: {', '.join(missing)}",
                file=sys.stderr,
            )
            return 2
        out_path = write_updater_manifest(
            version=args.version,
            installer_path=Path(args.installer),
            signature_path=Path(args.signature),
            out_path=Path(args.out),
            notes=args.notes,
            url=args.url,
        )
        print(f"[prepare_release] wrote updater manifest: {out_path}", flush=True)
        return 0

    return run_checks(skip_smoke=args.skip_smoke, skip_frontend=args.skip_frontend)


if __name__ == "__main__":
    raise SystemExit(main())
