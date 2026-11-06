#!/usr/bin/env python3
"""Assemble the Python runtime the installed app runs on. Standard library only.

    python scripts/build_release.py                 # stage the runtime under build/runtime
    python scripts/build_release.py --clean         # stage it again from scratch
    python scripts/build_release.py --bundle        # then build the installer (npm run tauri build)

The installed app carries its own interpreter: the official embeddable
distribution plus LitLoop's core dependencies, in `python/` next to the
executable (see `resolve_python()` in `app/src-tauri/src/lib.rs`). This script
builds that folder and checks it can `import litloop` before anything is
packaged. Optional extras (patchright and its browser, CloakBrowser, the layout
model) are NOT included; the app downloads them on first use.
"""

from __future__ import annotations

import argparse
import os
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
RUNTIME = BUILD / "runtime"
CACHE = BUILD / "cache"

# Pinned on purpose: the bundle ships exactly the interpreter it was built and
# tested with. Bump it (and re-run) when the project moves to a new Python.
PYTHON_VERSION = "3.14.3"


def step(msg: str) -> None:
    print(f"[release] {msg}", flush=True)


def venv_python() -> str:
    """The interpreter running this script — it installs the dependencies."""
    return sys.executable


def core_dependencies() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return list(data["project"]["dependencies"])


def download(url: str, dest: Path, timeout: int = 300) -> Path:
    """Fetch `url` into `dest` (cached). curl when urllib cannot verify a proxy.

    A corporate or local proxy hands out a self-signed certificate: urllib
    refuses it, curl trusts the Windows certificate store, so it is the fallback.
    """
    if dest.is_file():
        return dest
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    step(f"downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.URLError as exc:
        curl = shutil.which("curl")
        if not curl:
            sys.exit(f"[release] cannot download {url}: {exc}")
        step(f"urllib could not ({exc.reason}); trying curl")
        done = subprocess.run([curl, "-L", "--fail", "--silent", "--show-error", "-o", str(tmp), url])
        if done.returncode != 0:
            sys.exit(f"[release] curl could not download {url} (exit {done.returncode})")
    tmp.replace(dest)
    return dest


def fetch_python_zip() -> Path:
    """The official embeddable distribution, downloaded once into build/cache."""
    name = f"python-{PYTHON_VERSION}-embed-amd64.zip"
    return download(f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{name}", CACHE / name, timeout=120)


def unpack_python(zip_path: Path) -> None:
    step(f"unpacking {zip_path.name} into {RUNTIME}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(RUNTIME)


def install_dependencies(site: Path) -> None:
    """Install into the staged runtime, not into the host interpreter.

    The host may be a different Python (CI builds with 3.13), so plain `pip
    --target` would fetch the wrong wheels (numpy cp313 *.pyd inside a 3.14
    runtime, which fails at import). uv is fetched first and asked for the
    embedded interpreter explicitly; without uv, pip is told the target ABI.
    """
    deps = core_dependencies()
    step(f"installing {len(deps)} core dependencies into {site}")
    site.mkdir(parents=True, exist_ok=True)
    embedded = RUNTIME / "python.exe"
    uv = RUNTIME / "uv.exe"
    if uv.exists():
        cmd = [str(uv), "pip", "install", "--python", str(embedded),
               "--target", str(site), *deps]
        env = {**os.environ, "UV_LINK_MODE": "copy", "UV_CACHE_DIR": str(CACHE / "uv")}
        subprocess.run(cmd, check=True, env=env)
        return
    major, minor = PYTHON_VERSION.split(".")[:2]
    cmd = [venv_python(), "-m", "pip", "install", "--no-warn-script-location",
           "--disable-pip-version-check", "--only-binary", ":all:",
           "--platform", "win_amd64", "--implementation", "cp",
           "--python-version", f"{major}.{minor}", "--target", str(site), *deps]
    subprocess.run(cmd, check=True)


def install_litloop(site: Path) -> None:
    """Copy the package itself (its data files and templates live inside it)."""
    step("copying src/litloop into the runtime")
    dest = site / "litloop"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ROOT / "src" / "litloop", dest,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def drop_layout(site: Path) -> None:
    """Leave the layout model out: it is 67 MB and the app can do without it.

    pymupdf4llm 2.x pulls pymupdf-layout, which needs onnxruntime and networkx.
    Without them PDF-to-markdown falls back to plain reading order (see
    `download/markdown.py`), and the component is a download-on-demand item in
    the release plan. `--with-layout` keeps it for a local comparison.
    """
    freed = 0
    for pattern in ("onnxruntime", "networkx", "pymupdf_layout"):
        for path in [p for p in site.glob(pattern) if p.is_dir()]:
            freed += size_of(path)
            shutil.rmtree(path, ignore_errors=True)
        for path in [p for p in site.glob(f"{pattern}-*.dist-info") if p.is_dir()]:
            freed += size_of(path)
            shutil.rmtree(path, ignore_errors=True)
    step(f"dropped the layout model ({freed / 1e6:.0f} MB); plain text order is used instead")


def prune(site: Path) -> None:
    """Drop files the app never reads, because the installer pays per file.

    Windows Installer writes every file separately and the antivirus inspects
    each one: 5300 files took about 90 seconds to install on the author's
    machine, with the progress bar sitting at zero the whole time. Stubs,
    headers, bundled test suites and byte-code caches are never imported.
    """
    step("dropping files the app never reads")
    removed = freed = 0
    for path in list(site.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        parts = {part.lower() for part in path.parts}
        if "__pycache__" in parts or "tests" in parts or "test" in parts:
            pass
        elif name.endswith((".pyc", ".pyi", ".h")):
            pass
        else:
            continue
        freed += path.stat().st_size
        path.unlink()
        removed += 1
    for path in site.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    step(f"dropped {removed} files ({freed / 1e6:.0f} MB); what remains is imported at runtime")


UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"



def fetch_uv() -> Path:
    """uv.exe ships next to python.exe: the app installs optional components with it."""
    return download(UV_URL, CACHE / "uv-x86_64-pc-windows-msvc.zip")


def install_uv() -> None:
    step("adding uv.exe to the runtime")
    with zipfile.ZipFile(fetch_uv()) as zf:
        for name in zf.namelist():
            if name.rsplit("/", 1)[-1] == "uv.exe":
                with zf.open(name) as src, (RUNTIME / "uv.exe").open("wb") as out:
                    shutil.copyfileobj(src, out)
                return
    sys.exit("[release] uv-x86_64-pc-windows-msvc.zip has no uv.exe")


def write_path_file() -> None:
    """`python314._pth` decides what the embeddable interpreter can see."""
    major, minor = PYTHON_VERSION.split(".")[:2]
    tag = f"python{major}{minor}"
    pth = RUNTIME / f"{tag}._pth"
    lines = [
        f"{tag}.zip",
        ".",
        r"Lib\site-packages",
        "import site",
    ]
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    step(f"wrote {pth.name}")


def verify() -> None:
    exe = RUNTIME / "python.exe"
    check = "import litloop, pymupdf, pymupdf4llm, fastapi, mcp; print(litloop.__version__)"
    step("checking the runtime can import litloop")
    out = subprocess.run([str(exe), "-c", check], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"[release] the runtime does not import litloop:\n{out.stdout}\n{out.stderr}")
    step(f"runtime OK: litloop {out.stdout.strip()}")


def size_of(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def bundle() -> None:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    step("building the installer (npm run tauri build)")
    # The runtime only exists after staging, so the resources that pull it into
    # the bundle live in a separate overlay the plain `cargo check` never sees.
    subprocess.run([npm, "run", "tauri", "build", "--", "--config", "src-tauri/tauri.release.json"],
                   cwd=ROOT / "app", check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", action="store_true", help="delete build/runtime and stage it again")
    ap.add_argument("--bundle", action="store_true", help="run npm run tauri build afterwards")
    ap.add_argument("--with-layout", action="store_true",
                    help="keep pymupdf-layout and onnxruntime (about 67 MB) in the runtime")
    ap.add_argument("--no-uv", action="store_true",
                    help="do not ship uv.exe (optional components can then not be installed in the app)")
    args = ap.parse_args()

    if args.clean and RUNTIME.exists():
        step(f"removing {RUNTIME}")
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    site = RUNTIME / "Lib" / "site-packages"

    unpack_python(fetch_python_zip())
    if not args.no_uv:
        install_uv()          # before the dependencies: they are installed with it
    install_dependencies(site)
    install_litloop(site)
    if not args.with_layout:
        drop_layout(site)
    prune(site)
    write_path_file()
    verify()
    step(f"runtime is {size_of(RUNTIME) / 1e6:.0f} MB at {RUNTIME}")

    if args.bundle:
        bundle()


if __name__ == "__main__":
    main()
