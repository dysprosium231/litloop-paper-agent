"""Main conversational agent engine with tiered read-only vs. confirmed actions."""

from __future__ import annotations

import inspect
import json
import queue
import threading
import uuid
from typing import Any, Iterator

from .. import db
from ..terms import glossary
from . import runner
from .client import PROVIDER_PRESETS, chat_stream, estimate_cost, model_config


def chat_model_info(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return active chat model, thinking effort, and suggested models for the drawer switcher."""
    if config is None:
        from ..config import load_config

        config = load_config()
    cfg = model_config(config)
    preset_id = str(config.get("builtin_agent_preset") or "").strip().lower()
    base_url = cfg["base_url"].rstrip("/").lower()
    matched_preset = next(
        (
            p
            for p in PROVIDER_PRESETS
            if (preset_id and p["id"] == preset_id)
            or (base_url and str(p.get("base_url") or "").rstrip("/").lower() == base_url)
        ),
        None,
    )
    plan_model = str(config.get("builtin_agent_plan_model") or "").strip()
    rate_model = str(config.get("builtin_agent_rate_model") or "").strip()
    candidates: list[str] = [cfg["model"], plan_model, rate_model]
    if matched_preset:
        candidates.extend(
            [
                str(matched_preset.get("model") or ""),
                str(matched_preset.get("plan_model") or ""),
                str(matched_preset.get("rate_model") or ""),
            ]
        )
    suggestions: list[str] = []
    for m in candidates:
        s = m.strip()
        if s and s not in suggestions:
            suggestions.append(s)
    return {
        "provider": cfg["provider"],
        "protocol": cfg["protocol"],
        "preset": preset_id or (str(matched_preset["id"]) if matched_preset else ""),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "plan_model": plan_model,
        "rate_model": rate_model,
        "reasoning_effort": cfg["reasoning_effort"],
        "suggestions": suggestions,
    }

_MAX_TOOL_ROUNDS = 5
_ABSTRACT_LIMIT = 1500
_MAX_HISTORY_CHARS = 12000
_MAX_TOOL_RESULT_CHARS = 8000

_SYSTEM_PROMPT = """你是 LitLoop 文献助手，负责协助用户梳理检索课题、分析当前筛选结果、解释文献内容并规划下一步检索、下载或归档操作。

必须严格遵守以下红线：
1. 永不替用户直接做最终纳入（include）或排除（exclude）决定；你可以给出相关性分析与参考意见，最终决定权属于用户。
2. 严禁编造任何文献标题、作者、年份、期刊或 DOI；只引用上下文或工具返回的真实记录。
3. 所有会发起检索、续跑检索、批量下载、推送 Zotero 或修改术语表的操作，都必须通过 `propose_*` 工具生成确认卡片，等待用户点击确认，绝不能声称已经直接执行。
4. 回答简洁、准确、使用简体中文。"""

_TOOL_LABELS: dict[str, str] = {
    "get_session_overview": "读取会话概览",
    "list_papers": "查询筛选文献",
    "lookup_glossary": "查询术语表",
    "preview_search_plan": "预览检索方案",
    "get_download_status": "查询下载任务状态",
    "get_zotero_status": "查询 Zotero 状态",
    "propose_start_search": "生成发起检索确认卡",
    "propose_resume_search": "生成续跑检索确认卡",
    "propose_download": "生成下载确认卡",
    "propose_retry_download": "生成重试下载确认卡",
    "propose_zotero_push": "生成 Zotero 推送确认卡",
    "propose_add_glossary": "生成添加术语确认卡",
    "propose_batch_screen": "生成批量调整筛选结果确认卡",
}

CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_session_overview",
        "description": "读取指定检索会话的课题、纳入标准、筛选进度与纠偏备注概览（只读）。",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID，默认为当前会话。"},
            },
        },
    },
    {
        "name": "list_papers",
        "description": "按相关度、纳入决定或关键词筛选当前会话中的文献列表（只读）。",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID，默认为当前会话。"},
                "relevance": {"type": "string", "description": "按相关度过滤，如 高、中、低、不相关。"},
                "decision": {"type": "string", "description": "按用户决定过滤，如 selected（已纳入）、rejected（已排除）、undecided（未定）。"},
                "query": {"type": "string", "description": "在标题、摘要或 DOI 中搜索关键词。"},
                "limit": {"type": "integer", "description": "最多返回条数，默认 15。"},
            },
        },
    },
    {
        "name": "lookup_glossary",
        "description": "查询用户与内置的学术术语同义词/缩写表（只读）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要查找的词或缩写，留空返回全部在用条目。"},
            },
        },
    },
    {
        "name": "preview_search_plan",
        "description": "根据问题与纳入标准预览检索词、核心概念词与积分估算，不消耗检索额度（只读）。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "检索课题描述。"},
                "mode": {"type": "string", "enum": ["quick", "deep"], "description": "检索模式，默认 deep。"},
                "criteria": {"type": "string", "description": "可选的纳入标准文本。"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_download_status",
        "description": "查询当前会话或指定下载任务的进度、出版商排队与失败原因明细（只读）。",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "关联的筛选会话 ID，默认为当前会话。"},
                "job": {"type": "string", "description": "可选，指定下载任务 ID。"},
                "limit": {"type": "integer", "description": "未指定 job 时最多返回的任务条数，默认 10。"},
            },
        },
    },
    {
        "name": "get_zotero_status",
        "description": "查询 Zotero MCP 连接状态、待推送队列、推送历史及现有分类路径列表（只读）。",
        "parameters": {
            "type": "object",
            "properties": {
                "include_collections": {"type": "boolean", "description": "是否读取 Zotero 分类列表，默认 true。"},
            },
        },
    },
    {
        "name": "propose_start_search",
        "description": "提议发起一次新的文献检索（生成确认卡等待用户确认，不直接执行）。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "检索课题。"},
                "mode": {"type": "string", "enum": ["quick", "deep"], "description": "检索模式，默认 deep。"},
                "criteria": {"type": "string", "description": "纳入标准。"},
                "queries": {"type": "array", "items": {"type": "string"}, "description": "英文检索词列表。"},
                "core_terms": {"type": "array", "items": {"type": "string"}, "description": "核心概念过滤词。"},
                "batches": {"type": "integer", "description": "深度检索批次数，默认 3。"},
                "limit": {"type": "integer", "description": "快速检索条数上限，默认 20。"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "propose_resume_search",
        "description": "提议续跑已有深度检索会话，可附带新的纠偏备注（生成确认卡等待用户确认）。",
        "parameters": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "目标会话 ID，默认为当前会话。"},
                "batches": {"type": "integer", "description": "续跑批次数，默认 2。"},
                "steering_note": {"type": "string", "description": "可选的纠偏备注。"},
            },
        },
    },
    {
        "name": "propose_download",
        "description": "提议下载一批文献 PDF（生成确认卡等待用户确认，不直接执行）。",
        "parameters": {
            "type": "object",
            "properties": {
                "papers": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "直接指定的文献对象列表，每项至少包含 doi 与 title；留空时按 session + selection/paper_ns 自动选取。",
                },
                "title": {"type": "string", "description": "下载任务标题。"},
                "session": {"type": "string", "description": "关联的筛选会话 ID，默认为当前会话。"},
                "selection": {
                    "type": "string",
                    "enum": ["selected", "high", "high_and_medium"],
                    "description": "未传 papers 时的自动筛选范围：selected（已纳入）/high（高相关）/high_and_medium（中高相关）。",
                },
                "paper_ns": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "未传 papers 时按文献编号 n 精确选取。",
                },
                "skip_downloaded": {
                    "type": "boolean",
                    "description": "是否跳过本机已有或已下载过的文献，默认 true。",
                },
            },
        },
    },
    {
        "name": "propose_retry_download",
        "description": "提议重试下载任务中失败的文献，或启动指定出版商的排队队列（生成确认卡等待用户确认）。",
        "parameters": {
            "type": "object",
            "properties": {
                "job": {"type": "string", "description": "下载任务 ID。"},
                "mode": {
                    "type": "string",
                    "enum": ["failed", "publisher_queue"],
                    "description": "重试模式：failed（重试失败文献，默认）或 publisher_queue（启动出版商队列）。",
                },
                "publisher": {"type": "string", "description": "mode 为 publisher_queue 时必填的出版商名称。"},
            },
            "required": ["job"],
        },
    },
    {
        "name": "propose_zotero_push",
        "description": "提议将已完成下载的任务推送到 Zotero（生成确认卡等待用户确认）。",
        "parameters": {
            "type": "object",
            "properties": {
                "job": {"type": "string", "description": "下载任务 ID。"},
                "collection": {"type": "string", "description": "目标 Zotero 分类名。"},
            },
            "required": ["job"],
        },
    },
    {
        "name": "propose_add_glossary",
        "description": "提议向术语表添加一组同义词/缩写（生成确认卡等待用户确认）。",
        "parameters": {
            "type": "object",
            "properties": {
                "terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "同一概念的不同写法（至少 2 个）。",
                },
                "note": {"type": "string", "description": "可选备注说明。"},
            },
            "required": ["terms"],
        },
    },
    {
        "name": "propose_batch_screen",
        "description": "提议批量调整当前会话中若干篇文献的相关度评分或纳入/排除决定（生成确认卡等待用户确认）。",
        "parameters": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "目标会话 ID，默认为当前会话。"},
                "summary": {"type": "string", "description": "一句话说明批量调整原因。"},
                "items": {
                    "type": "array",
                    "description": "待调整的文献列表，每项指定文献编号 n 与目标字段。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "n": {"type": "integer", "description": "文献编号 n。"},
                            "relevance": {
                                "type": "string",
                                "enum": ["高", "中", "低", "不相关"],
                                "description": "可选，目标相关度评分。",
                            },
                            "decision": {
                                "type": ["string", "null"],
                                "enum": ["selected", "rejected", None],
                                "description": "可选，目标决定（selected/rejected/null）。",
                            },
                            "reject_reason": {"type": "string", "description": "可选，排除原因。"},
                            "note": {"type": "string", "description": "可选，单篇调整原因说明。"},
                        },
                        "required": ["n"],
                    },
                },
            },
            "required": ["items"],
        },
    },
]

_PROPOSE_SPECS: dict[str, tuple[str, str]] = {
    "propose_start_search": ("start_search", "发起文献检索"),
    "propose_resume_search": ("resume_search", "续跑深度检索"),
    "propose_download": ("download", "下载文献 PDF"),
    "propose_retry_download": ("retry_download", "重试下载 / 启动队列"),
    "propose_zotero_push": ("zotero_push", "推送到 Zotero"),
    "propose_add_glossary": ("add_glossary", "添加术语同义词"),
    "propose_batch_screen": ("batch_screen", "批量调整筛选结果"),
}


def _summarize_session(session_id: str) -> dict[str, Any]:
    screening = db.load_screening(session_id)
    summary = db.load_summary(session_id)
    notes = db.list_steering_notes(session_id, active_only=True)
    papers = screening.get("items") or screening.get("papers") or []
    rel_counts: dict[str, int] = {}
    user_changed = 0
    for p in papers:
        rel = str(p.get("user_relevance") or p.get("relevance") or "未评分").strip() or "未评分"
        rel_counts[rel] = rel_counts.get(rel, 0) + 1
        if p.get("user_relevance") and p.get("user_relevance") != p.get("relevance"):
            user_changed += 1
    return {
        "session_id": session_id,
        "question": screening.get("question") or "",
        "mode": screening.get("mode") or "quick",
        "criteria": screening.get("criteria") or [],
        "scored_count": len(papers),
        "relevance_counts": rel_counts,
        "user_changed_count": user_changed,
        "summary": summary,
        "steering_notes": [n["note"] for n in notes],
    }


def build_context_block(session_id: str, view: dict[str, Any] | None = None) -> str:
    """Build a concise context block with session progress, steering notes, and focused paper."""
    parts: list[str] = []
    if session_id and session_id != "__draft__" and db.session_exists(session_id):
        ov = _summarize_session(session_id)
        lines = [f"当前会话：{session_id}（模式：{ov['mode']}）"]
        if ov["question"]:
            lines.append(f"检索课题：{ov['question']}")
        if ov["criteria"]:
            lines.append("纳入标准：" + "；".join(str(c) for c in ov["criteria"]))
        rel_desc = "，".join(f"{k} {v} 篇" for k, v in ov["relevance_counts"].items())
        progress = f"已评分 {ov['scored_count']} 篇"
        if rel_desc:
            progress += f"（{rel_desc}）"
        if ov["user_changed_count"]:
            progress += f"，其中用户手动改分 {ov['user_changed_count']} 篇"
        lines.append(f"筛选进度：{progress}")
        summary = ov.get("summary")
        if isinstance(summary, dict) and summary:
            pool_total = summary.get("pool") or summary.get("pool_total")
            screened = summary.get("screened")
            remaining = summary.get("remaining")
            pool_bits = []
            if pool_total is not None:
                pool_bits.append(f"候选池 {pool_total}")
            if screened is not None:
                pool_bits.append(f"已筛 {screened}")
            if remaining is not None:
                pool_bits.append(f"待筛 {remaining}")
            if pool_bits:
                lines.append("候选池概况：" + "，".join(pool_bits))
        if ov["steering_notes"]:
            lines.append("活跃纠偏备注：" + "；".join(ov["steering_notes"]))
        parts.append("\n".join(lines))
        try:
            from ..desktop import download_jobs

            sess_jobs = [j for j in download_jobs.list_jobs(limit=30) if j.get("session") == session_id]
            if sess_jobs:
                latest = sess_jobs[0]
                dl_lines = [
                    f"关联下载任务：最新 {latest['job']}（状态：{latest.get('state')}，完成 {latest.get('done', 0)}/{latest.get('total', 0)}，失败 {latest.get('failed', 0)}，排队 {latest.get('queued', 0)}）"
                ]
                pending_z = [p for p in download_jobs.zotero_pending() if p.get("job") == latest.get("job")]
                if pending_z:
                    dl_lines.append(
                        f"待推送 Zotero：{len(pending_z[0].get('papers') or [])} 篇（目标：{pending_z[0].get('collection')}）"
                    )
                parts.append("\n".join(dl_lines))
        except Exception:
            pass
    else:
        notes = db.list_steering_notes(session_id, active_only=True) if session_id else []
        if notes:
            parts.append("活跃纠偏备注：" + "；".join(n["note"] for n in notes))

    current_paper = (view or {}).get("current_paper") if isinstance(view, dict) else None
    if isinstance(current_paper, dict) and current_paper:
        p_lines = ["当前正在查看的文献："]
        if current_paper.get("title"):
            p_lines.append(f"- 标题：{current_paper['title']}")
        meta = [str(current_paper[k]) for k in ("year", "journal", "doi") if current_paper.get(k)]
        if meta:
            p_lines.append("- 元数据：" + " | ".join(meta))
        rel = current_paper.get("relevance") or ""
        urel = current_paper.get("user_relevance") or ""
        if rel or urel:
            p_lines.append(f"- 评分：Agent={rel or '无'}，用户={urel or '未改'}")
        if current_paper.get("reason"):
            p_lines.append(f"- 评分理由：{current_paper['reason']}")
        abstract = str(current_paper.get("abstract") or "").strip()
        if abstract:
            if len(abstract) > _ABSTRACT_LIMIT:
                abstract = abstract[:_ABSTRACT_LIMIT] + "…"
            p_lines.append(f"- 摘要：{abstract}")
        parts.append("\n".join(p_lines))

    return "\n\n".join(parts)


_DECISION_ALIASES: dict[str, str] = {
    "selected": "selected",
    "include": "selected",
    "included": "selected",
    "keep": "selected",
    "rejected": "rejected",
    "exclude": "rejected",
    "excluded": "rejected",
    "undecided": "undecided",
    "none": "undecided",
    "pending": "undecided",
}


def _truncate_tool_result(text: str, limit: int = _MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...(已截断)"


def _trim_history(messages: list[dict[str, Any]], max_chars: int = _MAX_HISTORY_CHARS) -> list[dict[str, Any]]:
    if not messages:
        return []
    kept: list[dict[str, Any]] = []
    total = 0
    for m in reversed(messages):
        text = str(m.get("text") or "")
        size = len(text)
        if kept and total + size > max_chars:
            break
        kept.append(m)
        total += size
    kept.reverse()
    return kept


def _exec_readonly_tool(
    name: str,
    args: dict[str, Any],
    default_session: str,
    config: dict[str, Any] | None,
) -> str:
    if name == "get_session_overview":
        sid = str(args.get("session_id") or default_session)
        if not sid or sid == "__draft__" or not db.session_exists(sid):
            return json.dumps({"session_id": sid, "exists": False}, ensure_ascii=False)
        return _truncate_tool_result(json.dumps({"exists": True, **_summarize_session(sid)}, ensure_ascii=False))

    if name == "list_papers":
        sid = str(args.get("session_id") or default_session)
        if not sid or sid == "__draft__" or not db.session_exists(sid):
            return json.dumps({"papers": [], "total": 0}, ensure_ascii=False)
        screening = db.load_screening(sid)
        papers = screening.get("items") or screening.get("papers") or []
        rel_filter = str(args.get("relevance") or "").strip()
        raw_dec = str(args.get("decision") or "").strip().lower()
        dec_filter = _DECISION_ALIASES.get(raw_dec, raw_dec)
        q = str(args.get("query") or "").strip().lower()
        limit = max(1, min(50, int(args.get("limit") or 15)))
        matched: list[dict[str, Any]] = []
        for p in papers:
            eff_rel = str(p.get("user_relevance") or p.get("relevance") or "").strip()
            if rel_filter and eff_rel != rel_filter:
                continue
            item_dec = str(p.get("decision") or "").strip()
            if dec_filter == "undecided":
                if item_dec:
                    continue
            elif dec_filter and item_dec != dec_filter:
                continue
            if q:
                hay = " ".join(
                    str(p.get(k) or "") for k in ("title", "abstract", "doi", "journal")
                ).lower()
                if q not in hay:
                    continue
            matched.append(
                {
                    "n": p.get("n"),
                    "doi": p.get("doi"),
                    "title": p.get("title"),
                    "year": p.get("year"),
                    "journal": p.get("journal"),
                    "relevance": p.get("relevance"),
                    "user_relevance": p.get("user_relevance"),
                    "decision": p.get("decision"),
                    "reason": p.get("reason"),
                }
            )
        return _truncate_tool_result(
            json.dumps(
                {"total": len(matched), "papers": matched[:limit]},
                ensure_ascii=False,
            )
        )

    if name == "lookup_glossary":
        q = str(args.get("query") or "").strip().lower()
        entries = glossary.in_use()
        if q:
            entries = [
                e
                for e in entries
                if any(q in str(t).lower() for t in (e.get("terms") or []))
                or q in str(e.get("note") or "").lower()
            ]
        return _truncate_tool_result(json.dumps({"entries": entries[:30]}, ensure_ascii=False))

    if name == "preview_search_plan":
        form = {
            "question": str(args.get("question") or "").strip(),
            "mode": "quick" if args.get("mode") == "quick" else "deep",
            "criteria": str(args.get("criteria") or "").strip(),
        }
        plan = runner.plan_search(form, config=config)
        return _truncate_tool_result(json.dumps(plan, ensure_ascii=False))

    if name == "get_download_status":
        from ..desktop import download_jobs

        job_id = str(args.get("job") or "").strip()
        if job_id:
            data = download_jobs.get(job_id)
            items = data.get("items") or []
            counts = {
                "total": len(items),
                "done": sum(1 for i in items if i.get("status") == "done"),
                "failed": sum(1 for i in items if i.get("status") == "failed"),
                "queued": sum(1 for i in items if i.get("status") == "queued"),
                "waiting": sum(1 for i in items if i.get("status") == "waiting"),
            }
            failed_items = [
                {
                    "doi": i.get("doi"),
                    "title": i.get("title"),
                    "error": i.get("error"),
                    "error_text": i.get("error_text"),
                    "error_hint": i.get("error_hint"),
                }
                for i in items
                if i.get("status") == "failed"
            ]
            return _truncate_tool_result(
                json.dumps(
                    {
                        "job": data.get("job") or job_id,
                        "title": data.get("title"),
                        "session": data.get("session"),
                        "state": data.get("state"),
                        "counts": counts,
                        "queues": data.get("queues") or [],
                        "failed_items": failed_items,
                        "zotero": data.get("zotero"),
                    },
                    ensure_ascii=False,
                )
            )

        sid = str(args.get("session_id") or default_session)
        limit = max(1, min(50, int(args.get("limit") or 10)))
        all_jobs = download_jobs.list_jobs(limit=50)
        matched = [j for j in all_jobs if j.get("session") == sid] if sid and sid != "__draft__" else []
        session_matched = bool(matched)
        jobs_out = matched[:limit] if session_matched else all_jobs[:limit]
        return _truncate_tool_result(
            json.dumps(
                {
                    "session_id": sid,
                    "session_matched": session_matched,
                    "jobs": jobs_out,
                },
                ensure_ascii=False,
            )
        )

    if name == "get_zotero_status":
        from ..config import load_config
        from ..desktop import download_jobs
        from ..zotero import client as zotero_client

        cfg = config if config is not None else load_config()
        mcp_found = bool(zotero_client.discover_zotero_mcp(cfg))
        out: dict[str, Any] = {
            "mcp_found": mcp_found,
            "pending": download_jobs.zotero_pending(),
            "history": download_jobs.zotero_history()[:10],
        }
        if default_session and default_session != "__draft__":
            question = ""
            if db.session_exists(default_session):
                question = str(db.load_screening(default_session).get("question") or "").strip()
            out["recommended_collection"] = download_jobs.default_collection(question or default_session)

        include_cols = args.get("include_collections", True)
        if include_cols and mcp_found:
            try:
                col_res = zotero_client.zotero_collections(cfg)
                if col_res.get("success"):
                    raw_cols = col_res.get("collections") or []
                    out["collections"] = [
                        (c.get("path") or c.get("name")) if isinstance(c, dict) else str(c)
                        for c in raw_cols
                    ]
                else:
                    out["collections"] = []
                    out["collections_error"] = col_res.get("error") or "读取分类失败"
            except Exception as exc:
                out["collections"] = []
                out["collections_error"] = str(exc)
        else:
            out["collections"] = []
        return _truncate_tool_result(json.dumps(out, ensure_ascii=False))

    raise ValueError(f"未知只读工具：{name}")


def _build_action_card(name: str, args: dict[str, Any], default_session: str) -> dict[str, Any]:
    kind, title = _PROPOSE_SPECS[name]
    params = dict(args)
    if kind == "resume_search" and not params.get("session"):
        params["session"] = default_session
    if kind == "download" and "session" not in params and default_session != "__draft__":
        params["session"] = default_session

    if kind == "start_search":
        summary = f"模式：{params.get('mode', 'deep')} | 课题：{params.get('question', '')}"
    elif kind == "resume_search":
        summary = f"会话：{params.get('session', '')} | 批次：{params.get('batches', 2)}"
        if params.get("steering_note"):
            summary += f" | 备注：{params['steering_note']}"
    elif kind == "download":
        from ..desktop import download_jobs

        target_session = str(params.get("session") or default_session)
        skip_downloaded = bool(params.get("skip_downloaded", True))
        missing_doi = 0
        skipped_downloaded = 0
        raw_papers = params.get("papers")
        session_question = ""

        if not raw_papers:
            raw_papers = []
            if target_session and target_session != "__draft__" and db.session_exists(target_session):
                screening = db.load_screening(target_session)
                session_question = str(screening.get("question") or "").strip()
                all_items = screening.get("items") or screening.get("papers") or []
                paper_ns = params.get("paper_ns")
                if isinstance(paper_ns, list) and paper_ns:
                    wanted_ns: set[int] = set()
                    for n in paper_ns:
                        try:
                            wanted_ns.add(int(n))
                        except (TypeError, ValueError):
                            continue
                    candidates = []
                    for it in all_items:
                        try:
                            item_n = int(it.get("n"))
                        except (TypeError, ValueError):
                            continue
                        if item_n in wanted_ns:
                            candidates.append(it)
                else:
                    selection = str(params.get("selection") or "selected").strip()
                    if selection == "high":
                        candidates = [
                            it
                            for it in all_items
                            if str(it.get("user_relevance") or it.get("relevance") or "").strip() == "高"
                        ]
                    elif selection == "high_and_medium":
                        candidates = [
                            it
                            for it in all_items
                            if str(it.get("user_relevance") or it.get("relevance") or "").strip() in ("高", "中")
                        ]
                    else:
                        candidates = [it for it in all_items if it.get("decision") == "selected"]

                for it in candidates:
                    doi = str(it.get("doi") or "").strip()
                    if not doi:
                        missing_doi += 1
                        continue
                    raw_papers.append(
                        {
                            "doi": doi,
                            "title": it.get("title") or doi,
                            "year": it.get("year"),
                            "journal": it.get("journal"),
                        }
                    )
        else:
            normalized_raw: list[dict[str, Any]] = []
            for p in raw_papers:
                if not isinstance(p, dict):
                    continue
                doi = str(p.get("doi") or "").strip()
                if not doi:
                    missing_doi += 1
                    continue
                normalized_raw.append(
                    {
                        "doi": doi,
                        "title": p.get("title") or doi,
                        "year": p.get("year"),
                        "journal": p.get("journal"),
                    }
                )
            raw_papers = normalized_raw

        if skip_downloaded and raw_papers:
            got_dois = download_jobs.downloaded_dois()
            final_papers: list[dict[str, Any]] = []
            for p in raw_papers:
                if download_jobs.same_paper(p["doi"]) & got_dois:
                    skipped_downloaded += 1
                else:
                    final_papers.append(p)
        else:
            final_papers = list(raw_papers)

        dl_title = str(params.get("title") or "").strip() or session_question or (
            target_session if target_session and target_session != "__draft__" else "文献下载"
        )
        preview_titles = [str(p.get("title") or p.get("doi") or "") for p in final_papers[:5]]
        params = {
            "papers": final_papers,
            "title": dl_title,
            "skipped_downloaded": skipped_downloaded,
            "missing_doi": missing_doi,
            "preview_titles": preview_titles,
        }
        if target_session and target_session != "__draft__":
            params["session"] = target_session

        summary = f"任务：{dl_title}（{len(final_papers)} 篇）"
        if skipped_downloaded:
            summary += f" | 跳过已有 {skipped_downloaded} 篇"
        if missing_doi:
            summary += f" | 缺 DOI {missing_doi} 篇"
    elif kind == "retry_download":
        from ..desktop import download_jobs

        job_id = str(params.get("job") or "").strip()
        mode = "publisher_queue" if params.get("mode") == "publisher_queue" else "failed"
        publisher = str(params.get("publisher") or "").strip()
        try:
            data = download_jobs.get(job_id) if job_id else {}
        except (OSError, ValueError):
            data = {}
        items = data.get("items") or []
        if mode == "publisher_queue":
            count = sum(
                1
                for it in items
                if it.get("status") == "queued"
                and (it.get("publisher") or download_jobs.OTHER_PUBLISHER) == publisher
            )
            summary = f"任务：{job_id} | 启动出版商队列：{publisher}（{count} 篇）"
        else:
            count = sum(1 for it in items if it.get("status") == "failed")
            summary = f"任务：{job_id} | 失败文献重试（{count} 篇）"
        params = {"job": job_id, "mode": mode, "count": count}
        if publisher:
            params["publisher"] = publisher
    elif kind == "zotero_push":
        summary = f"任务：{params.get('job', '')}"
        if params.get("collection"):
            summary += f" → 分类：{params['collection']}"
    elif kind == "batch_screen":
        target_session = str(params.get("session") or default_session)
        by_n: dict[int, dict[str, Any]] = {}
        if target_session and target_session != "__draft__" and db.session_exists(target_session):
            screening = db.load_screening(target_session)
            for it in screening.get("items") or screening.get("papers") or []:
                try:
                    item_n = int(it["n"])
                except (KeyError, TypeError, ValueError):
                    continue
                by_n[item_n] = it

        normalized_items: list[dict[str, Any]] = []
        for raw in params.get("items") or []:
            if not isinstance(raw, dict):
                continue
            try:
                n_val = int(raw["n"])
            except (KeyError, TypeError, ValueError):
                continue
            hit = by_n.get(n_val)
            if hit is None:
                continue
            entry: dict[str, Any] = {
                "n": n_val,
                "doi": hit.get("doi") or "",
                "title": hit.get("title") or "",
                "cur_relevance": hit.get("user_relevance") or hit.get("relevance") or "",
                "cur_decision": hit.get("decision"),
            }
            if "relevance" in raw and raw.get("relevance") not in (None, ""):
                entry["relevance"] = str(raw["relevance"])
            if "decision" in raw:
                dec = raw.get("decision")
                if isinstance(dec, str):
                    dec_norm = _DECISION_ALIASES.get(dec.strip().lower(), dec.strip().lower())
                    entry["decision"] = None if dec_norm == "undecided" or not dec_norm else dec_norm
                else:
                    entry["decision"] = None
            if "reject_reason" in raw and raw.get("reject_reason") not in (None, ""):
                entry["reject_reason"] = str(raw["reject_reason"])
            if "note" in raw and raw.get("note") not in (None, ""):
                entry["note"] = str(raw["note"])
            normalized_items.append(entry)

        reason_summary = str(params.get("summary") or "").strip()
        params = {
            "session": target_session,
            "summary": reason_summary,
            "items": normalized_items,
        }
        summary = f"共 {len(normalized_items)} 篇"
        if reason_summary:
            summary += f" | {reason_summary}"
    else:
        terms = params.get("terms") or []
        summary = " / ".join(str(t) for t in terms)

    return {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "title": title,
        "summary": summary,
        "params": params,
        "status": "pending",
        "result": None,
    }


def _stream_round(
    messages: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None,
    system: str,
) -> Iterator[tuple[str, Any]]:
    q: queue.Queue[tuple[str, Any]] = queue.Queue()

    def _run() -> None:
        try:
            stream_kwargs: dict[str, Any] = {
                "config": config,
                "system": system,
                "max_tokens": 2048,
                "timeout": 120,
                "on_delta": lambda chunk: q.put(("delta", chunk)),
            }
            try:
                sig = inspect.signature(chat_stream)
                if "on_reasoning" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    stream_kwargs["on_reasoning"] = lambda r: q.put(("reasoning", r))
            except (TypeError, ValueError):
                pass
            reply = chat_stream(messages, CHAT_TOOLS, **stream_kwargs)
            q.put(("reply", reply))
        except Exception as exc:
            q.put(("error", exc))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    while True:
        kind, payload = q.get()
        if kind in ("delta", "reasoning"):
            yield (kind, payload)
        elif kind == "reply":
            yield ("reply", payload)
            break
        elif kind == "error":
            raise payload


def stream_chat_turn(
    session_id: str,
    user_text: str,
    *,
    view: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one conversational turn with live SSE deltas, read-only tools, and pending action cards."""
    user_msg = db.add_agent_message(session_id, "user", user_text)
    yield {"type": "user_message", "message": user_msg}

    try:
        cfg = model_config(config)
        protocol = cfg["protocol"]
        model_name = cfg.get("model") or ""

        history = db.list_agent_messages(session_id, limit=20)
        past = [
            {"role": m["role"], "text": m["content"]}
            for m in history
            if m["id"] != user_msg["id"] and m["role"] in ("user", "assistant")
        ]
        transcript: list[dict[str, Any]] = _trim_history([*past, {"role": "user", "text": user_text}])

        ctx_block = build_context_block(session_id, view)
        system = _SYSTEM_PROMPT + (f"\n\n{ctx_block}" if ctx_block else "")

        full_text_parts: list[str] = []
        pending_actions: list[dict[str, Any]] = []
        usage_by_model: dict[str, dict[str, int]] = {}

        for step in range(1, _MAX_TOOL_ROUNDS + 1):
            reply: dict[str, Any] | None = None
            for ev_kind, ev_val in _stream_round(
                runner._messages_for(protocol, transcript),
                config=config,
                system=system,
            ):
                if ev_kind == "delta":
                    yield {"type": "delta", "text": ev_val}
                elif ev_kind == "reasoning":
                    yield {"type": "reasoning", "text": ev_val}
                elif ev_kind == "reply":
                    reply = ev_val

            assert reply is not None
            runner._accumulate_usage(usage_by_model, model_name, reply.get("usage"))
            round_text = reply.get("text") or ""
            if round_text:
                full_text_parts.append(round_text)

            calls = reply.get("tool_calls") or []
            if not calls:
                break

            step_calls: list[dict[str, Any]] = []
            for idx, call in enumerate(calls):
                name = str(call.get("name") or "")
                call_id = str(call.get("id") or f"chat_{step}_{idx}")
                args = call.get("arguments") or {}
                if not isinstance(args, dict):
                    args = {}

                if name in _PROPOSE_SPECS:
                    card = _build_action_card(name, args, session_id)
                    pending_actions.append(card)
                    yield {"type": "action", "action": card}
                    result_str = "已生成确认卡片等待用户确认"
                else:
                    label = _TOOL_LABELS.get(name, name)
                    yield {"type": "tool", "name": name, "label": label}
                    try:
                        result_str = _exec_readonly_tool(name, args, session_id, config)
                    except Exception as exc:
                        result_str = f"出错：{exc}"

                step_calls.append(
                    {
                        "id": call_id,
                        "name": name,
                        "arguments": args,
                        "result": result_str,
                    }
                )

            turn_entry: dict[str, Any] = {"role": "assistant", "text": round_text, "calls": step_calls}
            if reply.get("reasoning"):
                turn_entry["reasoning"] = str(reply["reasoning"])
            transcript.append(turn_entry)

        usage_summary = estimate_cost(usage_by_model) if usage_by_model else None
        assistant_msg = db.add_agent_message(
            session_id,
            "assistant",
            "".join(full_text_parts),
            actions=pending_actions,
            usage=usage_summary,
        )
        yield {"type": "done", "message": assistant_msg}
    except Exception as exc:
        yield {"type": "error", "error": str(exc)}


def execute_action(
    session_id: str,
    message_id: int,
    action_id: str,
    *,
    edited_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a pending confirmation card action and persist its outcome."""
    messages = db.list_agent_messages(session_id, limit=500)
    target_msg = next((m for m in messages if m["id"] == int(message_id)), None)
    if target_msg is None:
        raise ValueError(f"未找到消息：{message_id}")
    action = next(
        (a for a in (target_msg.get("actions") or []) if isinstance(a, dict) and a.get("id") == action_id),
        None,
    )
    if action is None:
        raise ValueError(f"未找到动作：{action_id}")
    if action.get("status") != "pending":
        raise ValueError(f"动作状态为 {action.get('status')}，无法执行")

    params = {**(action.get("params") or {}), **(edited_params or {})}
    kind = str(action.get("kind") or "")
    try:
        if kind == "start_search":
            from ..desktop import agent_runner

            form = dict(params)
            if form.get("queries") and not form.get("planned_queries"):
                form["planned_queries"] = form.pop("queries")
            if form.get("core_terms") and not form.get("planned_core_terms"):
                form["planned_core_terms"] = form.pop("core_terms")
            if session_id == "__draft__":
                form["from_draft"] = True
            result: Any = agent_runner.start(form)
        elif kind == "resume_search":
            from ..desktop import agent_runner

            target_session = str(params.get("session") or session_id)
            note = str(params.get("steering_note") or "").strip()
            if note:
                db.add_steering_note(target_session, note)
            batches = int(params.get("batches") or 0)
            result = agent_runner.resume(target_session, batches=batches)
        elif kind == "download":
            from ..desktop import download_jobs

            papers = list(params.get("papers") or [])
            title = str(params.get("title") or "文献下载")
            dl_session = params.get("session")
            if dl_session is None and session_id != "__draft__":
                dl_session = session_id
            result = download_jobs.start(papers, title, session=dl_session)
        elif kind == "retry_download":
            from ..desktop import download_jobs

            job_id = str(params.get("job") or "").strip()
            mode = str(params.get("mode") or "failed")
            if mode == "publisher_queue":
                publisher = str(params.get("publisher") or "").strip()
                if not publisher:
                    raise ValueError("启动出版商队列时必须指定 publisher")
                job_data = download_jobs.start_queue(job_id, publisher)
                result = {
                    "job": job_data.get("job") or job_id,
                    "mode": mode,
                    "publisher": publisher,
                    "message": f"已启动 {publisher} 队列下载",
                }
            else:
                job_data = download_jobs.retry_failed(job_id)
                new_job = job_data.get("job") or ""
                result = {
                    "job": new_job,
                    "mode": mode,
                    "count": len(job_data.get("items") or []),
                    "message": f"已创建重试下载任务 {new_job}",
                }
        elif kind == "zotero_push":
            from ..desktop import download_jobs

            job = str(params.get("job") or "")
            collection = params.get("collection")
            result = download_jobs.push_zotero(job, collection)
        elif kind == "add_glossary":
            terms = [str(t) for t in (params.get("terms") or [])]
            note = str(params.get("note") or "")
            result = glossary.add(terms, note=note)
        elif kind == "batch_screen":
            target_session = str(params.get("session") or session_id)
            raw_items = params.get("items") or []
            ops: list[dict[str, Any]] = []
            for it in raw_items:
                if not isinstance(it, dict):
                    continue
                op: dict[str, Any] = {
                    "doi": it.get("doi"),
                    "title": it.get("title"),
                }
                if "relevance" in it and it.get("relevance") not in (None, ""):
                    op["relevance"] = str(it["relevance"])
                if "decision" in it:
                    dec = it.get("decision")
                    if isinstance(dec, str):
                        dec_norm = _DECISION_ALIASES.get(dec.strip().lower(), dec.strip().lower())
                        op["decision"] = None if dec_norm == "undecided" or not dec_norm else dec_norm
                    else:
                        op["decision"] = None
                if "reject_reason" in it and it.get("reject_reason") not in (None, ""):
                    op["reject_reason"] = str(it["reject_reason"])
                ops.append(op)
            if not ops:
                raise ValueError("请至少保留 1 篇待调整的文献")
            res = db.batch_update_items(target_session, ops)
            result = {
                "updated": res.get("updated", len(ops)),
                "count": len(ops),
                "message": f"已更新 {len(ops)} 篇筛选记录",
            }
        else:
            raise ValueError(f"未知动作类型：{kind}")
    except Exception as exc:
        updated = db.update_agent_action(message_id, action_id, "failed", {"error": str(exc)})
        return {"ok": False, "action": updated, "result": None, "error": str(exc)}

    stored_result = result if isinstance(result, dict) else {"value": result}
    updated = db.update_agent_action(message_id, action_id, "confirmed", stored_result)
    return {"ok": True, "action": updated, "result": result, "error": None}
