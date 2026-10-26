"""After close_browsers_since kills a driver, browser calls fail at once instead of spinning."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

SCRIPT = textwrap.dedent("""
    import time
    from litloop.browser import backend as bb
    try:
        b = bb.launch(headless=True)
    except Exception as e:
        print("NOBROWSER", e)
        raise SystemExit(0)
    page = b.new_context().new_page()
    bb.close_browsers_since(0.0)
    time.sleep(1)
    t = time.time()
    try:
        page.title()
        print("TITLE returned")
    except Exception as e:
        print("TITLE raised", type(e).__name__)
    b.close()
    print(f"CLOSED {time.time() - t:.1f}s")
""")


def test_calls_after_driver_killed_return(tmp_path):
    try:
        out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True,
                             encoding="utf-8", timeout=60, env=None)
    except subprocess.TimeoutExpired:
        pytest.fail("a browser call spun after its driver was killed")
    if "NOBROWSER" in out.stdout:
        pytest.skip("no browser backend here")
    assert "CLOSED" in out.stdout, out.stdout + out.stderr
