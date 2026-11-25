"""Concurrent paper relevance scoring pipeline for the built-in agent."""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable

from .. import db
from .client import chat, estimate_cost, model_config, normalize_usage

VALID_RELEVANCE = frozenset({"高", "中", "低", "不相关"})

_SYSTEM_PROMPT = (
    "你是严谨的学术文献相关性评审助手。根据研究问题与纳入标准，对每篇待筛文献给出相关性评级与一句话理由。\n"
    "相关度四档定义：\n"
    "- 高：直接研究目标问题或核心方法/体系，完全符合纳入标准。\n"
    "- 中：部分涉及目标问题、方法或相关体系，有参考价值。\n"
    "- 低：仅边缘提及关键词，核心主题不同。\n"
    "- 不相关：主题、对象或方法明显无关。\n"
    "只输出一个合法 JSON 对象，不要输出任何额外解释或 markdown 标记，格式固定为：\n"
    '{"ratings": [{"n": 1, "relevance": "高", "reason": "一句话中文理由"}]}'
)

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def collect_user_feedback(session: str, limit: int = 8) -> list[dict[str, Any]]:
    """Collect recent user-corrected items from a screening session as few-shot examples."""
    if not session or limit <= 0:
        return []
    data = db.load_screening(session)
    items = data.get("items") or []
    matched: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        agent_rel = str(item.get("relevance") or "").strip()
        user_rel = str(item.get("user_relevance") or "").strip()
        reject_reason = str(item.get("reject_reason") or "").strip()
        if (user_rel and user_rel != agent_rel) or reject_reason:
            matched.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "agent_relevance": agent_rel,
                    "user_relevance": user_rel,
                    "reason": str(item.get("reason") or "").strip(),
                    "reject_reason": reject_reason,
                }
            )
    return matched[-limit:]


def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return s


def _parse_chunk_ratings(raw_text: str, expected_ns: set[int]) -> dict[int, dict[str, Any]]:
    cleaned = _strip_fence(raw_text)
    parsed: Any = None
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        m = _JSON_OBJ_RE.search(cleaned)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except ValueError:
                parsed = None
    if not isinstance(parsed, dict):
        return {}

    out: dict[int, dict[str, Any]] = {}
    for row in parsed.get("ratings") or []:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("n"))
        except (TypeError, ValueError):
            continue
        if n not in expected_ns:
            continue
        rel = str(row.get("relevance") or "").strip()
        if rel not in VALID_RELEVANCE:
            continue
        out[n] = {
            "n": n,
            "relevance": rel,
            "reason": str(row.get("reason") or "").strip(),
        }
    return out


def _format_paper_block(n: int, paper: dict[str, Any]) -> str:
    title = str(paper.get("title") or "").strip() or "（无标题）"
    year = paper.get("year") or "年份未知"
    journal = str(paper.get("journal") or "").strip() or "期刊未知"
    rank = str(paper.get("journal_rank") or "").strip()
    journal_part = f"{journal} [{rank}]" if rank else journal
    abstract = str(paper.get("abstract") or "").strip()
    if abstract:
        abstract_part = abstract[:1200]
    else:
        abstract_part = "（无摘要）"
    return f"[{n}] 标题：{title}\n年份：{year} | 期刊：{journal_part}\n摘要：{abstract_part}"


def _build_user_prompt(
    question: str,
    criteria: list[str],
    chunk: list[tuple[int, dict[str, Any]]],
    few_shots: list[dict[str, Any]] | None,
    steering_notes: list[str] | None,
) -> str:
    parts: list[str] = [f"研究问题：{question}"]
    if criteria:
        parts.append("纳入标准：\n" + "\n".join(f"- {c}" for c in criteria if str(c).strip()))
    else:
        parts.append("纳入标准：无额外标准，请结合研究问题判断。")

    if steering_notes:
        notes = [str(s).strip() for s in steering_notes if str(s).strip()]
        if notes:
            parts.append("用户补充筛选指令：\n" + "\n".join(f"- {s}" for s in notes))

    if few_shots:
        shot_lines: list[str] = []
        for ex in few_shots:
            title = str(ex.get("title") or "").strip()
            a_rel = str(ex.get("agent_relevance") or "").strip()
            u_rel = str(ex.get("user_relevance") or "").strip()
            rej = str(ex.get("reject_reason") or "").strip()
            line = f"- 《{title}》：模型原判={a_rel or '未评'}，用户改判={u_rel or '未改'}"
            if rej:
                line += f"（用户说明：{rej}）"
            shot_lines.append(line)
        if shot_lines:
            parts.append("用户历史纠偏示例（请遵循用户偏好）：\n" + "\n".join(shot_lines))

    parts.append(
        "待评文献列表（请逐篇评分，返回所有编号的 JSON）：\n\n"
        + "\n\n".join(_format_paper_block(n, p) for n, p in chunk)
    )
    return "\n\n".join(parts)


def _merge_usage(acc: dict[str, dict[str, int]], model: str, raw_usage: dict[str, Any] | None) -> None:
    norm = normalize_usage(raw_usage)
    if not model:
        return
    cur = acc.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    cur["input_tokens"] += norm["input_tokens"]
    cur["output_tokens"] += norm["output_tokens"]
    cur["total_tokens"] += norm["total_tokens"]


def score_papers(
    question: str,
    criteria: list[str],
    papers: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    few_shots: list[dict[str, Any]] | None = None,
    steering_notes: list[str] | None = None,
    chunk_size: int = 5,
    max_workers: int = 3,
    stop_event: threading.Event | None = None,
    on_chunk_done: Callable[[list[dict[str, Any]], int, int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Score papers concurrently in chunks, retrying missing items once per chunk."""
    if not papers:
        return {
            "ratings": [],
            "missing": [],
            "usage": estimate_cost({}),
            "cancelled": bool(stop_event and stop_event.is_set()),
        }

    indexed: list[tuple[int, dict[str, Any]]] = []
    for idx, paper in enumerate(papers, 1):
        n_val = paper.get("n")
        n = int(n_val) if n_val is not None else idx
        indexed.append((n, paper))

    ordered_ns = [n for n, _ in indexed]
    total_count = len(indexed)
    step = max(1, int(chunk_size or 5))
    chunks = [indexed[i : i + step] for i in range(0, total_count, step)]

    rate_model = model_config(config, task="rate").get("model") or "unknown"
    lock = threading.Lock()
    results_by_n: dict[int, dict[str, Any]] = {}
    total_usage: dict[str, dict[str, int]] = {}
    cancelled = bool(stop_event and stop_event.is_set())

    def _call_chunk(items: list[tuple[int, dict[str, Any]]]) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, int]]]:
        prompt = _build_user_prompt(question, criteria, items, few_shots, steering_notes)
        reply = chat(
            [{"role": "user", "content": prompt}],
            tools=None,
            config=config,
            system=_SYSTEM_PROMPT,
            max_tokens=1600,
            timeout=120,
            task="rate",
        )
        chunk_usage: dict[str, dict[str, int]] = {}
        _merge_usage(chunk_usage, rate_model, reply.get("usage"))
        expected = {n for n, _ in items}
        parsed = _parse_chunk_ratings(str(reply.get("text") or ""), expected)
        return parsed, chunk_usage

    def _process_chunk(chunk: list[tuple[int, dict[str, Any]]]) -> None:
        nonlocal cancelled
        if stop_event and stop_event.is_set():
            with lock:
                cancelled = True
            return

        chunk_usage: dict[str, dict[str, int]] = {}
        try:
            parsed, u1 = _call_chunk(chunk)
        except Exception:
            parsed, u1 = {}, {}
        for m, u in u1.items():
            _merge_usage(chunk_usage, m, u)

        missing_items = [(n, p) for n, p in chunk if n not in parsed]
        if missing_items and not (stop_event and stop_event.is_set()):
            try:
                retried, u2 = _call_chunk(missing_items)
                parsed.update(retried)
            except Exception:
                u2 = {}
            for m, u in u2.items():
                _merge_usage(chunk_usage, m, u)

        chunk_ratings = [parsed[n] for n, _ in chunk if n in parsed]
        chunk_cost = estimate_cost(chunk_usage)

        with lock:
            for r in chunk_ratings:
                results_by_n[r["n"]] = r
            for m, u in chunk_usage.items():
                _merge_usage(total_usage, m, u)
            done_count = len(results_by_n)
            if on_chunk_done and chunk_ratings:
                on_chunk_done(chunk_ratings, done_count, total_count, chunk_cost)

    workers = max(1, int(max_workers or 3))
    if not cancelled:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: set[Future[None]] = set()
            for chunk in chunks:
                if stop_event and stop_event.is_set():
                    cancelled = True
                    break
                pending.add(pool.submit(_process_chunk, chunk))
                while len(pending) >= workers:
                    done_set, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    if stop_event and stop_event.is_set():
                        cancelled = True
                        break
                    for fut in done_set:
                        fut.result()
                if cancelled:
                    break
            for fut in pending:
                fut.result()

    if stop_event and stop_event.is_set():
        cancelled = True

    ratings = [results_by_n[n] for n in ordered_ns if n in results_by_n]
    missing = [n for n in ordered_ns if n not in results_by_n]
    return {
        "ratings": ratings,
        "missing": missing,
        "usage": estimate_cost(total_usage),
        "cancelled": cancelled,
    }
