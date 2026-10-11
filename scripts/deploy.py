#!/usr/bin/env python3
"""Install or update litloop end to end. Standard library only.

    python scripts/deploy.py            # install, or update an existing checkout
    python scripts/deploy.py --dry-run  # show what would happen

Steps: sync the git checkout (survives force-pushed history), create/refresh a
dedicated venv, install the package with its extras, register the MCP server
with the AI clients found on this machine, copy the skills, then check the
environment and print a summary. Safe to re-run; an agent can run it for the
user and relay the summary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("litloop needs Python 3.11+; run this script with a newer python.")

REPO_URL = "https://github.com/dysprosium231/litloop-paper-agent.git"
FIND_URL = "git+https://github.com/dysprosium231/easyseek.git"
SERVER = "litloop"
SKILLS = ("litloop", "litloop-sort")
WIN = os.name == "nt"

HOME = Path.home()
ROOT = Path(__file__).resolve().parents[1]
REPORT: dict = {"steps": [], "warnings": [], "next": []}


def step(msg: str) -> None:
    REPORT["steps"].append(msg)
    print(f"[litloop] {msg}", flush=True)


def warn(msg: str) -> None:
    REPORT["warnings"].append(msg)
    print(f"[litloop] WARNING: {msg}", flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True,
        dry: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    if dry:
        print("  (dry-run) " + " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    proc = subprocess.run(cmd, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                          capture_output=capture)
    if check and proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-1500:]
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{tail}")
    return proc


def git(*args: str, check: bool = True) -> str:
    return run(["git", *args], cwd=ROOT, check=check).stdout.strip()


# --------------------------------------------------------------------------- git

def sync_checkout(branch: str, repo: str, dry: bool) -> bool:
    """Bring the checkout up to date. Returns True if files changed."""
    if not (ROOT / ".git").exists():
        warn(f"{ROOT} is not a git checkout; skipping update (install only)")
        return False
    if not shutil.which("git"):
        raise SystemExit("git not found; install Git first.")

    origin = git("remote", "get-url", "origin", check=False)
    if origin.rstrip("/").removesuffix(".git") != repo.rstrip("/").removesuffix(".git"):
        if dry:
            print(f"  (dry-run) git remote set-url origin {repo}")
        else:
            git("remote", "set-url", "origin", repo)
        step(f"origin was {origin or '(none)'}; now {repo}")

    if dry:
        print(f"  (dry-run) git fetch origin {branch}; fast-forward or reset")
        return False
    git("fetch", "origin", branch)
    before = git("rev-parse", "HEAD")
    remote = f"origin/{branch}"
    if git("rev-parse", "--abbrev-ref", "HEAD") != branch:
        git("checkout", branch)

    if run(["git", "merge-base", "--is-ancestor", "HEAD", remote], cwd=ROOT,
           check=False).returncode == 0:
        git("merge", "--ff-only", remote)
    else:
        # History was rewritten upstream (rebased fork) or there are local
        # commits. Reset only if every local commit already exists upstream
        # (as the same patch) and nothing is uncommitted.
        local_only = [ln for ln in git("cherry", remote, "HEAD").splitlines()
                      if ln.startswith("+")]
        dirty = git("status", "--porcelain", "--untracked-files=no")
        if local_only or dirty:
            raise SystemExit(
                f"{ROOT} has local changes not on {remote} "
                f"({len(local_only)} commits, {'dirty' if dirty else 'clean'} tree). "
                "Commit/push or discard them, then re-run.")
        git("reset", "--hard", remote)
    after = git("rev-parse", "HEAD")
    step(f"code at {after[:7]} ({'updated from ' + before[:7] if after != before else 'already current'})")
    return after != before


# -------------------------------------------------------------------------- venv

def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if WIN else "bin/python")


def registered_venv() -> Path | None:
    """The venv an existing Claude Code registration runs from, if it is ours."""
    claude = shutil.which("claude")
    if not claude:
        return None
    out = run([claude, "mcp", "get", SERVER], check=False).stdout
    cmd = next((ln.split(":", 1)[1].strip() for ln in out.splitlines()
                if ln.strip().startswith("Command:")), "")
    if not cmd:
        return None
    bin_dir = Path(cmd).parent
    if bin_dir.name.lower() not in ("scripts", "bin"):
        return None
    venv = bin_dir.parent
    py = venv_python(venv)
    if not py.exists():
        return None
    probe = run([str(py), "-c", "import litloop,pathlib;"
                 "print(pathlib.Path(litloop.__file__).resolve())"], check=False).stdout
    return venv if str(ROOT).lower() in probe.lower() else None


def ensure_venv(venv: Path, dry: bool) -> Path:
    py = venv_python(venv)
    if py.exists():
        out = run([str(py), "-c", "import sys;print(sys.version_info[:2]>=(3,11))"], check=False).stdout
        if "True" in out:
            step(f"venv ok: {venv}")
            return py
        warn(f"venv at {venv} is broken or too old; recreating")
        if not dry:
            shutil.rmtree(venv, ignore_errors=True)
    step(f"creating venv: {venv}")
    run([sys.executable, "-m", "venv", str(venv)], dry=dry)
    return py


def pip_install(py: Path, extras: str, with_find: bool, dry: bool) -> None:
    target = f"{ROOT}[{extras}]" if extras else str(ROOT)
    step(f"pip install -e {target}")
    proc = run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e", target],
               check=False, dry=dry)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "")[-1500:]
        if WIN and ("WinError 32" in msg or "WinError 5" in msg):
            raise SystemExit("A file in the venv is in use (the MCP server is running). "
                             "Quit Claude Code / Codex / Claude Desktop, then re-run.\n" + msg)
        raise SystemExit("pip install failed:\n" + msg)
    if with_find:
        step("installing scansci-find (easyseek fork)")
        run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-U", FIND_URL],
            dry=dry)


# ----------------------------------------------------------------------- clients

def server_cmd(py: Path) -> list[str]:
    # `python -m litloop` keeps the server off the console-script exe, so a
    # later pip upgrade does not hit a file locked by the running server.
    return [str(py), "-m", "litloop", "run"]


def _same_install(cmd: str, py: Path) -> bool:
    try:
        return Path(cmd).resolve().parent == py.resolve().parent
    except OSError:
        return False


def register_claude_code(py: Path, force: bool, dry: bool) -> None:
    claude = shutil.which("claude")
    if not claude:
        return
    cur = run([claude, "mcp", "get", SERVER], check=False)
    if cur.returncode == 0 and not force:
        cmd_line = next((ln.split(":", 1)[1].strip() for ln in cur.stdout.splitlines()
                         if ln.strip().startswith("Command:")), "")
        if cmd_line and Path(cmd_line).exists():
            note = "this venv" if _same_install(cmd_line, py) else cmd_line
            step(f"Claude Code: {SERVER} already registered ({note}); kept")
            return
        run([claude, "mcp", "remove", SERVER, "-s", "user"], check=False, dry=dry)
    elif cur.returncode == 0:
        run([claude, "mcp", "remove", SERVER, "-s", "user"], check=False, dry=dry)
    run([claude, "mcp", "add", "-s", "user", SERVER, "--", *server_cmd(py)], dry=dry)
    step("Claude Code: registered MCP server (user scope)")


def _json_register(path: Path, label: str, py: Path, force: bool, dry: bool) -> None:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        warn(f"{label}: {path} is not valid JSON; not touched")
        return
    servers = cfg.setdefault("mcpServers", {})
    if SERVER in servers and not force and Path(str(servers[SERVER].get("command", ""))).exists():
        step(f"{label}: {SERVER} already registered; kept")
        return
    cmd = server_cmd(py)
    servers[SERVER] = {"command": cmd[0], "args": cmd[1:]}
    if dry:
        print(f"  (dry-run) write {path}")
    else:
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    step(f"{label}: registered MCP server in {path}")


def register_claude_desktop(py: Path, force: bool, dry: bool, explicit: bool) -> None:
    candidates = []
    if WIN and os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json")
    candidates += [HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
                   HOME / ".config" / "Claude" / "claude_desktop_config.json"]
    for path in candidates:
        # In auto mode only touch a config that already exists (the user runs
        # MCP servers there); the directory alone also exists for the app's
        # Code tab, which uses Claude Code's registration instead.
        if path.is_file() or (explicit and path.parent.is_dir()):
            _json_register(path, "Claude Desktop", py, force, dry)
            return


def register_codex(py: Path, force: bool, dry: bool) -> None:
    codex = shutil.which("codex")
    if not codex:
        return
    listed = run([codex, "mcp", "list"], check=False).stdout
    if SERVER in listed and not force:
        step(f"Codex: {SERVER} already registered; kept")
        return
    if SERVER in listed:
        run([codex, "mcp", "remove", SERVER], check=False, dry=dry)
    proc = run([codex, "mcp", "add", SERVER, "--", *server_cmd(py)], check=False, dry=dry)
    if proc.returncode == 0:
        step("Codex: registered MCP server")
    else:
        warn("Codex: `codex mcp add` failed; on Windows scripts/install-litloop.ps1 "
             "registers it as a Codex plugin instead")


def install_skills(dry: bool) -> None:
    targets = []
    if (HOME / ".claude").is_dir():
        targets.append(HOME / ".claude" / "skills")
    if (HOME / ".codex").is_dir():
        targets.append(HOME / ".codex" / "skills")
    for base in targets:
        for name in SKILLS:
            src = ROOT / "skills" / name
            if not src.is_dir():
                continue
            dst = base / name
            if dry:
                print(f"  (dry-run) copy {src} -> {dst}")
                continue
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
            # Repo-relative links (../../docs/...) break once copied out.
            for md in dst.rglob("*.md"):
                text = md.read_text(encoding="utf-8")
                fixed = text.replace("../../", ROOT.as_posix() + "/")
                if fixed != text:
                    md.write_text(fixed, encoding="utf-8")
        step(f"skills copied to {base}")


# ------------------------------------------------------------------------ checks

def find_chrome() -> str:
    if WIN:
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base and (Path(base) / "Google/Chrome/Application/chrome.exe").exists():
                return str(Path(base) / "Google/Chrome/Application/chrome.exe")
        return ""
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac.exists():
        return str(mac)
    return shutil.which("google-chrome") or shutil.which("google-chrome-stable") or ""


def checks(py: Path, dry: bool) -> None:
    if dry:
        return
    proc = run([str(py), "-c", "import litloop;print(litloop.__version__)"], check=False)
    REPORT["version"] = proc.stdout.strip() or "?"
    if proc.returncode != 0:
        warn("package does not import: " + (proc.stderr or "")[-500:])
        return
    step(f"litloop {REPORT['version']} imports OK")

    chrome = find_chrome()
    REPORT["chrome"] = chrome
    if not chrome:
        warn("Google Chrome not found. Browser-based routes (publisher login, CARSI) "
             "use it; install Chrome, or run: "
             f"{py} -m patchright install chromium")

    probe = ("import json;from litloop.config import load_config;"
             "from litloop.zotero import discover_zotero_mcp as d;"
             "f=d(load_config());print(json.dumps(str(f['python']) if f else ''))")
    z = run([str(py), "-c", probe], check=False).stdout.strip().splitlines()
    zpath = json.loads(z[-1]) if z else ""
    REPORT["zotero_mcp"] = zpath
    if not zpath:
        REPORT["next"].append("Optional: install zotero-mcp (github.com/54yyyu/zotero-mcp) "
                              "to push papers + PDFs into Zotero; it is found automatically.")

    proc = run([str(py), "-m", "litloop", "check"], check=False)
    REPORT["check"] = (proc.stdout or proc.stderr or "").strip()[-2000:]


# -------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--venv", default="",
                    help="default: the venv already registered with Claude Code, "
                         "else ~/.litloop/venv")
    ap.add_argument("--extras", default="fast,vpnsci,patchright",
                    help="pip extras (add 'zotero' to bundle zotero-mcp)")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--repo", default=REPO_URL)
    ap.add_argument("--with-find", action="store_true",
                    help="also install scansci-find (journal-aware search)")
    ap.add_argument("--clients", default="auto",
                    help="comma list of claude-code,claude-desktop,codex, or auto / none")
    ap.add_argument("--force-register", action="store_true",
                    help="replace existing MCP registrations with this venv")
    ap.add_argument("--no-sync", action="store_true", help="do not git fetch/update")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()

    if not args.no_sync and sync_checkout(args.branch, args.repo, args.dry_run):
        # The new checkout may carry a newer version of this script.
        step("re-running the updated deploy script")
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()),
                                  *sys.argv[1:], "--no-sync"])

    venv = Path(args.venv).expanduser() if args.venv else (
        registered_venv() or HOME / ".litloop" / "venv")
    py = ensure_venv(venv, args.dry_run)
    pip_install(py, args.extras, args.with_find, args.dry_run)

    clients = {"claude-code", "claude-desktop", "codex"} if args.clients == "auto" else \
        set() if args.clients == "none" else {c.strip() for c in args.clients.split(",")}
    if "claude-code" in clients:
        register_claude_code(py, args.force_register, args.dry_run)
    if "claude-desktop" in clients:
        register_claude_desktop(py, args.force_register, args.dry_run,
                                explicit=args.clients != "auto")
    if "codex" in clients:
        register_codex(py, args.force_register, args.dry_run)
    if not any(r.startswith(("Claude", "Codex")) for r in REPORT["steps"]):
        REPORT["next"].append("No AI client was registered. Add this MCP server manually: "
                              + json.dumps({"command": server_cmd(py)[0], "args": server_cmd(py)[1:]}))
    if clients:
        install_skills(args.dry_run)
    checks(py, args.dry_run)

    REPORT["next"].insert(0, "Restart Claude Code / Codex / Claude Desktop so they load the new version.")
    REPORT["next"].append("Paywalled papers: set your school with litloop_schools and log in once "
                          "with litloop_login (you type the password in the browser yourself).")
    REPORT.update(checkout=str(ROOT), venv=str(venv), python=str(py))
    if args.json:
        print(json.dumps(REPORT, ensure_ascii=False, indent=2))
    else:
        print("\n== summary ==")
        for w in REPORT["warnings"]:
            print("! " + w)
        for n in REPORT["next"]:
            print("- " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
