"""Smoke check for the desktop app backend (and optional packaged exe / runtime).

Starts ``python -m litloop.desktop.server`` in an isolated temporary data
directory, verifies token auth (401 without token, 200 with ``X-LitLoop-Token``),
checks ``/api/health`` and ``/api/sessions``, and cleans up the child process.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_python_for_exe(exe_path: Path) -> Path:
    """Find the bundled Python next to an installed ``litloop-app.exe``."""
    exe_dir = exe_path.parent
    for rel in (Path("python/python.exe"), Path("resources/python/python.exe")):
        cand = exe_dir / rel
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"bundled python.exe not found beside {exe_path}")


def parse_port_line(line: str) -> int | None:
    """Parse ``{"litloop_app_server": 1, "port": <port>}`` emitted by desktop.server."""
    try:
        data = json.loads(line.strip())
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and data.get("litloop_app_server") == 1 and isinstance(data.get("port"), int):
        port = int(data["port"])
        if 1 <= port <= 65535:
            return port
    return None


def _http_request(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None, timeout: float = 3.0
) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return int(resp.status), hdrs, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        hdrs = {k.lower(): v for k, v in err.headers.items()} if err.headers else {}
        return int(err.code), hdrs, err.read().decode("utf-8", errors="replace")


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 3.0) -> tuple[int, str]:
    code, _, body = _http_request(url, method="GET", headers=headers, timeout=timeout)
    return code, body


def _terminate_tree(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def smoke_backend(python_exe: Path, *, timeout: float = 25.0) -> dict[str, Any]:
    """Launch ``litloop.desktop.server`` and verify ``/api/health`` + ``/api/sessions``."""
    token = secrets.token_hex(16)
    tmp_data = Path(tempfile.mkdtemp(prefix="litloop_smoke_"))
    env = dict(os.environ)
    env["LITLOOP_DATA_DIR"] = str(tmp_data)
    env["PYTHONUTF8"] = "1"
    # Ensure source checkout is importable if the target interpreter hasn't pip-installed litloop.
    src_dir = str(REPO_ROOT / "src")
    if (REPO_ROOT / "src" / "litloop").is_dir() and python_exe == Path(sys.executable):
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = src_dir if not existing_pp else f"{src_dir}{os.pathsep}{existing_pp}"

    proc = subprocess.Popen(
        [str(python_exe), "-m", "litloop.desktop.server", "--token", token],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    try:
        port_box: dict[str, int | None] = {"port": None}

        def _read_stdout() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                parsed = parse_port_line(line)
                if parsed is not None:
                    port_box["port"] = parsed
                    break

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stdout_thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_box["port"] is not None:
                break
            if proc.poll() is not None:
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                err = "".join(stderr_lines).strip()
                raise RuntimeError(f"backend exited early (code={proc.returncode}): {err}")
            time.sleep(0.05)

        port = port_box["port"]
        if port is None:
            raise TimeoutError(f"backend did not report port within {timeout}s")

        base = f"http://127.0.0.1:{port}"
        last_err = ""
        health_payload: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            try:
                code_no_tok, _ = _http_get(f"{base}/api/health")
                if code_no_tok != 401:
                    raise AssertionError(f"expected 401 without token, got {code_no_tok}")
                code_ok, body_ok = _http_get(f"{base}/api/health", headers={"X-LitLoop-Token": token})
                if code_ok == 200:
                    health_payload = json.loads(body_ok)
                    break
                last_err = f"HTTP {code_ok}: {body_ok}"
            except (urllib.error.URLError, OSError) as exc:
                last_err = str(exc)
            time.sleep(0.1)

        if health_payload is None:
            raise TimeoutError(f"/api/health not ready within {timeout}s ({last_err})")
        if not health_payload.get("version") or not isinstance(health_payload.get("schema"), int):
            raise AssertionError(f"unexpected /api/health payload: {health_payload!r}")

        sess_code, sess_body = _http_get(f"{base}/api/sessions", headers={"X-LitLoop-Token": token})
        if sess_code != 200 or not isinstance(json.loads(sess_body), list):
            raise AssertionError(f"/api/sessions check failed: {sess_code} {sess_body[:200]}")

        opt_code, opt_headers, opt_body = _http_request(
            f"{base}/api/sessions",
            method="OPTIONS",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-litloop-token",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        if opt_code != 200 or opt_headers.get("access-control-allow-private-network") != "true":
            raise AssertionError(
                f"WebView2 PNA preflight check failed: {opt_code} {opt_headers!r} {opt_body[:200]}"
            )

        return {
            "python": str(python_exe),
            "port": port,
            "version": health_payload["version"],
            "schema": health_payload["schema"],
        }
    finally:
        _terminate_tree(proc)
        shutil.rmtree(tmp_data, ignore_errors=True)


def smoke_launch_exe(exe_path: Path, *, stay_seconds: float = 3.0) -> None:
    """Start the installed desktop executable briefly to verify it stays alive."""
    tmp_data = Path(tempfile.mkdtemp(prefix="litloop_exe_smoke_"))
    env = dict(os.environ)
    env["LITLOOP_DATA_DIR"] = str(tmp_data)
    proc = subprocess.Popen([str(exe_path)], env=env)
    try:
        time.sleep(stay_seconds)
        if proc.poll() is not None:
            raise RuntimeError(f"{exe_path} exited prematurely with code {proc.returncode}")
    finally:
        _terminate_tree(proc)
        shutil.rmtree(tmp_data, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Smoke-test LitLoop app backend / packaged exe")
    ap.add_argument("--python", type=Path, help="Explicit Python interpreter to test")
    ap.add_argument("--runtime", action="store_true", help="Test packaged snapshot build/runtime/python.exe")
    ap.add_argument("--exe", type=Path, help="Installed litloop-app.exe (resolves bundled python)")
    ap.add_argument("--launch-exe", action="store_true", help="Also launch --exe briefly to verify startup")
    ap.add_argument("--stay-seconds", type=float, default=3.0, help="Seconds to keep launched exe running")
    ap.add_argument("--timeout", type=float, default=25.0, help="Backend startup timeout in seconds")
    args = ap.parse_args(argv)

    if args.exe:
        if not args.exe.is_file():
            print(f"FAIL: exe not found: {args.exe}", file=sys.stderr)
            return 1
        py_exe = resolve_python_for_exe(args.exe)
    elif args.runtime:
        py_exe = REPO_ROOT / "build" / "runtime" / "python.exe"
    elif args.python:
        py_exe = args.python
    else:
        py_exe = Path(sys.executable)

    if not py_exe.is_file():
        print(f"FAIL: Python interpreter not found: {py_exe}", file=sys.stderr)
        return 1

    try:
        info = smoke_backend(py_exe, timeout=args.timeout)
        print(
            f"OK: backend healthy on 127.0.0.1:{info['port']} "
            f"(version={info['version']}, schema={info['schema']}, python={info['python']})"
        )
        if args.exe and args.launch_exe:
            smoke_launch_exe(args.exe, stay_seconds=args.stay_seconds)
            print(f"OK: exe stayed alive for {args.stay_seconds:.1f}s ({args.exe})")
        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
