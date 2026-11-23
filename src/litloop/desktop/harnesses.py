"""Agent harness discovery, runtime invocation, and global MCP/skill sync.

Supports Claude Code, WorkBuddy, DeepSeek Harness (dsh), Codex CLI, the
built-in model API agent, and a custom command fallback.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from .. import config as _config

HARNESS_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "workbuddy": "WorkBuddy",
    "dsh": "DeepSeek Harness",
    "codex": "Codex CLI",
    "builtin": "内置 Agent",
    "custom": "自定义命令",
}

HARNESS_ORDER: tuple[str, ...] = ("claude", "workbuddy", "dsh", "codex", "builtin", "custom")
AUTO_PROBED_ORDER: tuple[str, ...] = ("claude", "workbuddy", "dsh", "codex")
SYNCABLE_HARNESSES: frozenset[str] = frozenset({"claude", "workbuddy", "dsh", "codex"})
RESUMABLE_HARNESSES: frozenset[str] = frozenset({"claude", "workbuddy", "dsh", "codex"})
VALID_HARNESS_IDS: frozenset[str] = frozenset({"auto", *HARNESS_ORDER})


def _home() -> Path:
    return Path.home()


def _write_text_lf(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(content.replace("\r\n", "\n"))


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _uninstall_install_dirs(display_keyword: str) -> list[Path]:
    """Query Windows Uninstall registry keys for entries whose DisplayName contains display_keyword."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    kw_lower = display_keyword.lower()
    found: list[Path] = []
    seen: set[str] = set()

    for hive, subkey_path in roots:
        try:
            with winreg.OpenKey(hive, subkey_path) as root_key:
                count = winreg.QueryInfoKey(root_key)[0]
                for idx in range(count):
                    try:
                        sub_name = winreg.EnumKey(root_key, idx)
                        with winreg.OpenKey(root_key, sub_name) as item_key:
                            name_val, _ = winreg.QueryValueEx(item_key, "DisplayName")
                            if not isinstance(name_val, str) or kw_lower not in name_val.lower():
                                continue
                            for val_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    raw, _ = winreg.QueryValueEx(item_key, val_name)
                                except OSError:
                                    continue
                                if not isinstance(raw, str) or not raw.strip():
                                    continue
                                cleaned = raw.strip().strip('"').split(",")[0].strip().strip('"')
                                p = Path(cleaned)
                                if p.suffix.lower() == ".exe":
                                    p = p.parent
                                norm = str(p).lower()
                                if norm not in seen and p.is_dir():
                                    seen.add(norm)
                                    found.append(p)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _find_claude_binary(home: Path) -> str | None:
    which = shutil.which("claude")
    if which:
        return which
    candidates = (
        home / ".local" / "bin" / "claude.exe",
        home / ".local" / "bin" / "claude",
        home / "AppData" / "Roaming" / "npm" / "claude.cmd",
        home / "AppData" / "Roaming" / "npm" / "claude",
    )
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def _system_install_dirs(kind: str) -> list[Path]:
    """System-wide install directories (Windows registry + Program Files)."""
    if kind == "workbuddy":
        return [
            *_uninstall_install_dirs("WorkBuddy"),
            Path(r"D:\Program Files\WorkBuddyAI"),
            Path(r"C:\Program Files\WorkBuddyAI"),
        ]
    if kind == "dsh":
        return [
            *_uninstall_install_dirs("DeepSeek Harness"),
            Path(r"D:\Program Files\DSH"),
            Path(r"C:\Program Files\DSH"),
        ]
    return []


def _find_workbuddy_paths(home: Path) -> tuple[str | None, str | None]:
    """Return (binary_path, cli_mjs_path).

    When the desktop WorkBuddy package is installed, binary_path is WorkBuddyAI.exe
    and cli_mjs_path is resources/app.asar.unpacked/cli/dist/codebuddy-lite-wb.mjs.
    When a standalone codebuddy/workbuddy CLI is on PATH, cli_mjs_path is None.
    """
    dirs: list[Path] = [
        home / "AppData" / "Local" / "Programs" / "WorkBuddyAI",
        home / "WorkBuddyAI",
        *_system_install_dirs("workbuddy"),
    ]
    seen: set[str] = set()
    for d in dirs:
        key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        exe = d / "WorkBuddyAI.exe"
        mjs = d / "resources" / "app.asar.unpacked" / "cli" / "dist" / "codebuddy-lite-wb.mjs"
        if exe.is_file() and mjs.is_file():
            return str(exe), str(mjs)

    for cmd in ("codebuddy", "workbuddy"):
        which = shutil.which(cmd)
        if which:
            return which, None
    return None, None


def _find_dsh_binary(home: Path) -> str | None:
    dirs: list[Path] = [
        home / "AppData" / "Local" / "Programs" / "DSH",
        home / "DSH",
        *_system_install_dirs("dsh"),
    ]
    seen: set[str] = set()
    for d in dirs:
        key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        for name in ("dsh.cmd", "dsh.exe", "dsh"):
            candidate = d / "resources" / "runtime" / "cli" / "bin" / name
            if candidate.is_file():
                return str(candidate)
    which = shutil.which("dsh")
    if which:
        return which
    return None


def _find_codex_binary(home: Path) -> str | None:
    which = shutil.which("codex")
    if which:
        return which
    for name in ("codex.cmd", "codex.exe", "codex"):
        candidate = home / "AppData" / "Roaming" / "npm" / name
        if candidate.is_file():
            return str(candidate)
    return None


def _has_json_mcp_litloop(path: Path) -> bool:
    data = _read_json_dict(path)
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and isinstance(servers.get("litloop"), dict)


def _has_dsh_patch_litloop(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "mcp-litloop" in text or "serverName: litloop" in text


def _has_codex_litloop(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "[mcp_servers.litloop]" in text


def _external_detail(label: str, binary: str | None, mcp_ok: bool, skill_ok: bool) -> str:
    if not binary:
        return f"未检测到 {label}"
    if mcp_ok and skill_ok:
        return f"已就绪（MCP 与技能已配置）· {binary}"
    if mcp_ok:
        return f"已安装（全局 MCP 已配置，技能待同步）· {binary}"
    if skill_ok:
        return f"已安装（技能已同步，全局 MCP 待同步）· {binary}"
    return f"已安装（app 内可直接调用；可一键同步全局配置）· {binary}"


def _probe_all(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    home = _home()

    # 1. Claude Code
    claude_bin = _find_claude_binary(home)
    claude_mcp = _has_json_mcp_litloop(home / ".claude.json")
    claude_skill = (home / ".claude" / "skills" / "litloop" / "SKILL.md").is_file()

    # 2. WorkBuddy
    wb_bin, wb_mjs = _find_workbuddy_paths(home)
    wb_mcp = (
        _has_json_mcp_litloop(home / ".workbuddy-ai" / "mcp.json")
        or _has_json_mcp_litloop(home / ".workbuddy-ai" / ".mcp.json")
        or _has_json_mcp_litloop(home / ".claude.json")
    )
    wb_skill = (home / ".workbuddy-ai" / "skills" / "litloop" / "SKILL.md").is_file()

    # 3. DeepSeek Harness (dsh)
    dsh_bin = _find_dsh_binary(home)
    dsh_desktop_patch = home / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
    dsh_headless_patch = home / ".dsh" / "profiles" / "headless" / "cordis.patch.yml"
    dsh_mcp = _has_dsh_patch_litloop(dsh_desktop_patch) and _has_dsh_patch_litloop(dsh_headless_patch)
    dsh_skill = (home / ".dsh" / "skills" / "litloop" / "SKILL.md").is_file()

    # 4. Codex CLI
    codex_bin = _find_codex_binary(home)
    codex_mcp = _has_codex_litloop(home / ".codex" / "config.toml")
    codex_skill = (home / ".codex" / "skills" / "litloop" / "SKILL.md").is_file()

    # 5. Built-in Agent
    builtin_model = str(cfg.get("builtin_agent_model") or "").strip()
    builtin_detail = (
        f"当前模型：{builtin_model}"
        if builtin_model
        else "在下方「内置 Agent」填写模型接口与 Key"
    )

    # 6. Custom Command
    custom_cmd = str(cfg.get("agent_command") or "claude").strip()
    custom_args = str(cfg.get("agent_args") or "").strip()
    custom_found = shutil.which(custom_cmd) or (custom_cmd if Path(custom_cmd).is_file() else None)
    custom_detail = f"{custom_cmd} {custom_args}".strip()

    return {
        "claude": {
            "id": "claude",
            "label": HARNESS_LABELS["claude"],
            "detected": bool(claude_bin),
            "selected": False,
            "binary": claude_bin,
            "version": None,
            "mcp_configured": claude_mcp,
            "skill_installed": claude_skill,
            "can_sync": True,
            "can_resume": True,
            "detail": _external_detail(HARNESS_LABELS["claude"], claude_bin, claude_mcp, claude_skill),
        },
        "workbuddy": {
            "id": "workbuddy",
            "label": HARNESS_LABELS["workbuddy"],
            "detected": bool(wb_bin),
            "selected": False,
            "binary": wb_bin,
            "version": None,
            "mcp_configured": wb_mcp,
            "skill_installed": wb_skill,
            "can_sync": True,
            "can_resume": True,
            "detail": _external_detail(HARNESS_LABELS["workbuddy"], wb_bin, wb_mcp, wb_skill),
            "_mjs": wb_mjs,
        },
        "dsh": {
            "id": "dsh",
            "label": HARNESS_LABELS["dsh"],
            "detected": bool(dsh_bin),
            "selected": False,
            "binary": dsh_bin,
            "version": None,
            "mcp_configured": dsh_mcp,
            "skill_installed": dsh_skill,
            "can_sync": True,
            "can_resume": True,
            "detail": _external_detail(HARNESS_LABELS["dsh"], dsh_bin, dsh_mcp, dsh_skill),
        },
        "codex": {
            "id": "codex",
            "label": HARNESS_LABELS["codex"],
            "detected": bool(codex_bin),
            "selected": False,
            "binary": codex_bin,
            "version": None,
            "mcp_configured": codex_mcp,
            "skill_installed": codex_skill,
            "can_sync": True,
            "can_resume": True,
            "detail": _external_detail(HARNESS_LABELS["codex"], codex_bin, codex_mcp, codex_skill),
        },
        "builtin": {
            "id": "builtin",
            "label": HARNESS_LABELS["builtin"],
            "detected": True,
            "selected": False,
            "binary": None,
            "version": None,
            "mcp_configured": True,
            "skill_installed": True,
            "can_sync": False,
            "can_resume": False,
            "detail": builtin_detail,
        },
        "custom": {
            "id": "custom",
            "label": HARNESS_LABELS["custom"],
            "detected": bool(custom_found),
            "selected": False,
            "binary": str(custom_found) if custom_found else None,
            "version": None,
            "mcp_configured": False,
            "skill_installed": False,
            "can_sync": False,
            "can_resume": False,
            "detail": custom_detail,
        },
    }


def _resolve_with_probed(cfg: dict[str, Any], probed: dict[str, dict[str, Any]]) -> str:
    backend = str(cfg.get("agent_backend") or "").strip().lower()
    harness = str(cfg.get("agent_harness") or "auto").strip().lower()
    if backend == "builtin" or harness == "builtin":
        return "builtin"
    if harness in ("claude", "workbuddy", "dsh", "codex", "custom"):
        return harness

    cmd = str(cfg.get("agent_command") or "claude").strip()
    cmd_name = Path(cmd).name.lower() if cmd else "claude"
    if cmd_name not in ("", "claude", "claude.exe", "claude.cmd"):
        if "dsh" in cmd_name:
            return "dsh"
        if "workbuddy" in cmd_name or "codebuddy" in cmd_name:
            return "workbuddy"
        if "codex" in cmd_name:
            return "codex"
        return "custom"

    for hid in AUTO_PROBED_ORDER:
        if probed.get(hid, {}).get("detected"):
            return hid
    return "claude"


def resolve_harness(cfg: dict[str, Any] | None = None) -> str:
    """Resolve the active harness ID from configuration and installed binaries."""
    active_cfg = cfg if cfg is not None else _config.load_config()
    probed = _probe_all(active_cfg)
    return _resolve_with_probed(active_cfg, probed)


def detect_harnesses(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Detect all supported agent harnesses and mark the currently selected one."""
    active_cfg = cfg if cfg is not None else _config.load_config()
    probed = _probe_all(active_cfg)
    selected_id = _resolve_with_probed(active_cfg, probed)
    out: list[dict[str, Any]] = []
    for hid in HARNESS_ORDER:
        info = {k: v for k, v in probed[hid].items() if not k.startswith("_")}
        info["selected"] = hid == selected_id
        out.append(info)
    return out


def _skill_source_files() -> list[Path]:
    here = Path(__file__).resolve()
    for parent in here.parents:
        skill_dir = parent / "skills" / "litloop"
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            files = [skill_md]
            ref_md = skill_dir / "reference.md"
            if ref_md.is_file():
                files.append(ref_md)
            return files
    return []


def _copy_skill_files(target_dir: Path) -> list[str]:
    sources = _skill_source_files()
    written: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    if sources:
        for src in sources:
            dst = target_dir / src.name
            _write_text_lf(dst, src.read_text(encoding="utf-8"))
            written.append(str(dst))
    else:
        dst = target_dir / "SKILL.md"
        if not dst.is_file():
            _write_text_lf(dst, "---\nname: litloop\ndescription: LitLoop literature search and download\n---\n")
        written.append(str(dst))
    return written


def _extract_dsh_llm_blocks(yaml_text: str) -> list[tuple[str, str]]:
    """Extract top-level `- id: llm-*` YAML blocks from a cordis.patch.yml file."""
    blocks: list[tuple[str, str]] = []
    lines = yaml_text.replace("\r\n", "\n").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^-\s+id:\s*(llm-[\w-]+)\s*$", line)
        if not m:
            i += 1
            continue
        llm_id = m.group(1)
        chunk = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.startswith("  "):
                chunk.append(nxt)
                i += 1
            elif not nxt.strip() and i + 1 < len(lines) and lines[i + 1].startswith("  "):
                chunk.append(nxt)
                i += 1
            else:
                break
        blocks.append((llm_id, "\n".join(chunk)))
    return blocks


def _format_dsh_mcp_litloop_block(python_cmd: str) -> str:
    py_posix = Path(python_cmd).as_posix()
    return "\n".join([
        "- insert:",
        "    - id: mcp-litloop",
        '      name: "@deepseek-ai/dsh-mcp-client"',
        "      config:",
        "        serverName: litloop",
        "        transport: stdio",
        f'        command: "{py_posix}"',
        '        args: [ "-m", "litloop", "run" ]',
    ])


def _merge_dsh_patch_text(
    existing_text: str,
    python_cmd: str,
    *,
    llm_blocks_to_sync: list[tuple[str, str]] | None = None,
) -> str:
    text = existing_text.replace("\r\n", "\n").rstrip()
    parts: list[str] = [text] if text else []

    if llm_blocks_to_sync and not re.search(r"(?m)^-\s+id:\s*llm-", text):
        for _, block in llm_blocks_to_sync:
            parts.append(block)

    if "mcp-litloop" not in text and "serverName: litloop" not in text:
        parts.append(_format_dsh_mcp_litloop_block(python_cmd))

    return "\n".join(parts).rstrip() + "\n"


def _sync_codex_config_toml(config_path: Path, python_cmd: str) -> None:
    py_posix = Path(python_cmd).as_posix()
    section = "\n".join([
        "[mcp_servers.litloop]",
        f'command = "{py_posix}"',
        'args = ["-m", "litloop", "run"]',
    ])
    if not config_path.is_file():
        _write_text_lf(config_path, section + "\n")
        return
    try:
        existing = config_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        existing = ""
    pattern = re.compile(r"(?ms)^\[mcp_servers\.litloop\]\s*\n.*?(?=^\[|\Z)")
    if pattern.search(existing):
        updated = pattern.sub(section + "\n\n", existing).rstrip() + "\n"
    else:
        updated = (existing.rstrip() + "\n\n" + section + "\n") if existing.strip() else (section + "\n")
    _write_text_lf(config_path, updated)


def sync_harness(harness_id: str) -> dict[str, Any]:
    """One-click global MCP and skill synchronization for an external harness."""
    hid = str(harness_id or "").strip().lower()
    if hid not in SYNCABLE_HARNESSES:
        raise ValueError(f"不支持同步此 Agent：{harness_id}")

    home = _home()
    python_cmd = sys.executable
    stdio_entry = {
        "type": "stdio",
        "command": python_cmd,
        "args": ["-m", "litloop", "run"],
    }
    files: list[str] = []

    if hid == "claude":
        mcp_path = home / ".claude.json"
        data = _read_json_dict(mcp_path)
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            data["mcpServers"] = servers
        servers["litloop"] = stdio_entry
        _write_text_lf(mcp_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        files.append(str(mcp_path))
        files.extend(_copy_skill_files(home / ".claude" / "skills" / "litloop"))

    elif hid == "workbuddy":
        mcp_path = home / ".workbuddy-ai" / "mcp.json"
        data = _read_json_dict(mcp_path)
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            data["mcpServers"] = servers
        servers["litloop"] = stdio_entry
        _write_text_lf(mcp_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        files.append(str(mcp_path))
        files.extend(_copy_skill_files(home / ".workbuddy-ai" / "skills" / "litloop"))

    elif hid == "dsh":
        desktop_patch = home / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
        headless_patch = home / ".dsh" / "profiles" / "headless" / "cordis.patch.yml"
        desktop_existing = desktop_patch.read_text(encoding="utf-8") if desktop_patch.is_file() else ""
        llm_blocks = _extract_dsh_llm_blocks(desktop_existing)

        desktop_updated = _merge_dsh_patch_text(desktop_existing, python_cmd)
        _write_text_lf(desktop_patch, desktop_updated)
        files.append(str(desktop_patch))

        headless_existing = headless_patch.read_text(encoding="utf-8") if headless_patch.is_file() else ""
        headless_updated = _merge_dsh_patch_text(
            headless_existing, python_cmd, llm_blocks_to_sync=llm_blocks
        )
        _write_text_lf(headless_patch, headless_updated)
        files.append(str(headless_patch))

        files.extend(_copy_skill_files(home / ".dsh" / "skills" / "litloop"))

    elif hid == "codex":
        config_toml = home / ".codex" / "config.toml"
        _sync_codex_config_toml(config_toml, python_cmd)
        files.append(str(config_toml))
        files.extend(_copy_skill_files(home / ".codex" / "skills" / "litloop"))

    updated_list = detect_harnesses()
    updated_info = next((h for h in updated_list if h["id"] == hid), None)
    label = HARNESS_LABELS.get(hid, hid)
    return {
        "ok": True,
        "harness": updated_info,
        "message": f"已配置 {label} 的全局 LitLoop 工具与技能",
        "files": files,
    }


def select_harness(harness_id: str) -> list[dict[str, Any]]:
    """Switch the active harness in config and return the updated harness list."""
    hid = str(harness_id or "").strip().lower()
    if hid not in VALID_HARNESS_IDS:
        raise ValueError(f"未知的 Agent 类型：{harness_id}")
    if hid == "builtin":
        _config.update_config("agent_backend", "builtin")
        _config.update_config("agent_harness", "builtin")
    else:
        _config.update_config("agent_backend", "command")
        _config.update_config("agent_harness", hid)
    return detect_harnesses()


def _write_runtime_mcp_json(run_dir: Path) -> Path:
    mcp_json = run_dir / "litloop-mcp.json"
    payload = {
        "mcpServers": {
            "litloop": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "litloop", "run"],
            }
        }
    }
    _write_text_lf(mcp_json, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return mcp_json


def _write_runtime_dsh_patch(run_dir: Path) -> Path:
    patch_yml = run_dir / "litloop-dsh.patch.yml"
    home = _home()
    desktop_patch = home / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
    desktop_text = desktop_patch.read_text(encoding="utf-8") if desktop_patch.is_file() else ""
    llm_blocks = _extract_dsh_llm_blocks(desktop_text)

    parts: list[str] = [block for _, block in llm_blocks]
    parts.append(_format_dsh_mcp_litloop_block(sys.executable))
    _write_text_lf(patch_yml, "\n".join(parts).rstrip() + "\n")
    return patch_yml


def build_invocation(
    cfg: dict[str, Any] | None = None,
    *,
    harness_id: str | None = None,
    resume_session: str | None = None,
) -> tuple[list[str], dict[str, str], str]:
    """Build (cmd_list, extra_env, resolved_harness_id) for launching an external harness."""
    active_cfg = cfg if cfg is not None else _config.load_config()
    probed = _probe_all(active_cfg)
    resolved = (
        str(harness_id).strip().lower()
        if harness_id and str(harness_id).strip().lower() in HARNESS_ORDER
        else _resolve_with_probed(active_cfg, probed)
    )
    run_dir = _config.DATA_DIR / "agent_runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    resume = str(resume_session or "").strip() or None

    if resolved == "claude":
        binary = probed["claude"].get("binary") or "claude"
        mcp_json = _write_runtime_mcp_json(run_dir)
        cmd = [
            str(binary),
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--allowedTools",
            "mcp__litloop__*",
            "Skill",
            "Read",
            "--mcp-config",
            str(mcp_json),
        ]
        if resume:
            cmd.extend(["--resume", resume])
        return cmd, {}, "claude"

    if resolved == "workbuddy":
        wb_info = probed["workbuddy"]
        binary = wb_info.get("binary") or "workbuddy"
        mjs = wb_info.get("_mjs")
        mcp_json = _write_runtime_mcp_json(run_dir)
        common_args = [
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--allowedTools",
            "mcp__litloop__*",
            "Skill",
            "Read",
            "--mcp-config",
            str(mcp_json),
        ]
        if resume:
            common_args.extend(["--resume", resume])
        if mjs:
            return [str(binary), str(mjs), *common_args], {"ELECTRON_RUN_AS_NODE": "1"}, "workbuddy"
        return [str(binary), *common_args], {}, "workbuddy"

    if resolved == "dsh":
        binary = probed["dsh"].get("binary") or "dsh"
        patch_yml = _write_runtime_dsh_patch(run_dir)
        cmd = [
            str(binary),
            "--profile",
            "headless",
            "--json",
            "--approval-policy",
            "never",
            "--patch",
            str(patch_yml),
        ]
        if resume:
            cmd.extend(["--session-id", resume])
        cmd.append("-")
        return cmd, {}, "dsh"

    if resolved == "codex":
        binary = probed["codex"].get("binary") or "codex"
        if resume:
            cmd = [str(binary), "exec", "resume", "--json", "--full-auto", resume, "-"]
        else:
            cmd = [str(binary), "exec", "--json", "--full-auto", "-"]
        return cmd, {}, "codex"

    # Fallback: custom command (or builtin if caller asked for command invocation)
    cmd_str = str(active_cfg.get("agent_command") or "claude").strip()
    exe = shutil.which(cmd_str) or cmd_str
    args = [
        a.strip('"')
        for a in shlex.split(str(active_cfg.get("agent_args") or ""), posix=os.name != "nt")
    ]
    return [exe, *args], {}, "custom"
