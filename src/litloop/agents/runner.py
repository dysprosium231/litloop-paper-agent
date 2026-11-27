"""Drive one task: ask the model, run the tools it calls, ask again.

The loop is protocol-agnostic; only the message shapes differ, so the runner
keeps a neutral transcript and converts it per turn. Tool functions are plain
callables that return JSON-serializable values; the schemas handed to the model
are the same ``{"name", "description", "parameters"}`` dicts ``chat()`` takes.
"""

from __future__ import annotations

import json
import math
import threading
from typing import Any, Callable

from .. import config as _config
from ..search import deep, quick, screening
from ..zotero import client as zotero_client
from .client import chat, estimate_cost, model_config, normalize_usage, strip_thinking
from .scorer import collect_user_feedback, score_papers

Tool = Callable[..., Any]
EventHandler = Callable[[str, dict[str, Any]], None]


class ToolError(Exception):
    """A tool the model asked for does not exist or could not do its job."""


def _json(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _call(tools: dict[str, Tool], name: str, arguments: dict[str, Any]) -> str:
    fn = tools.get(name)
    if fn is None:
        raise ToolError(f"没有这个工具：{name}")
    try:
        return _json(fn(**arguments))
    except TypeError as exc:  # wrong or missing arguments
        raise ToolError(f"{name} 的参数不对：{exc}") from exc


def _messages_for(protocol: str, transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Neutral turns -> the shape this protocol expects."""
    out: list[dict[str, Any]] = []
    for turn in transcript:
        role = turn["role"]
        if role in ("user", "system"):
            out.append({"role": role, "content": turn.get("text", "")})
            continue
        calls = turn.get("calls") or []
        if protocol == "anthropic":
            blocks: list[dict[str, Any]] = []
            if turn.get("text"):
                blocks.append({"type": "text", "text": turn["text"]})
            for call in calls:
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                               "input": call["arguments"]})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            results = [{"type": "tool_result", "tool_use_id": call["id"], "content": call["result"]}
                       for call in calls]
            if results:
                out.append({"role": "user", "content": results})
            continue
        msg: dict[str, Any] = {"role": "assistant", "content": turn.get("text") or (None if calls else "")}
        if calls:
            msg["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": _json(call["arguments"])},
                }
                for call in calls
            ]
        out.append(msg)
        for call in calls:
            out.append({"role": "tool", "tool_call_id": call["id"], "content": call["result"]})
    return out


_FETCH_TOOLS = frozenset({"search", "deep_search_start", "deep_search_next", "deep_search_status"})


def default_task_for_step(step: int, produced: list[dict[str, Any]]) -> str:
    """Use the planning model initially, and switch to the rating model right after batches/hits arrive."""
    if step <= 1 or not produced:
        return "plan"
    if str(produced[-1].get("name") or "") in _FETCH_TOOLS:
        return "rate"
    return "plan"


def estimate_search_credits(mode: str, query_count: int, batches: int) -> tuple[str, str]:
    """Rough OpenAlex credit range and a short Chinese explanation (0 credits spent to estimate)."""
    q = max(1, int(query_count or 1))
    if mode == "deep":
        b = max(1, int(batches or 3))
        low = min(300, 80 + q * 20)
        high = max(low + 100, min(1500, 120 + q * 30 + b * 90))
        return f"约 {low}–{high} 积分", f"按 {q} 组检索词、约 {b} 批扩展与补摘要粗略估算（免费 Key 每日 10,000 积分）"
    low = q * 10
    high = q * 40
    return f"约 {low}–{high} 积分", f"按 {q} 组检索词的快速查找粗略估算（免费 Key 每日 10,000 积分）"


def _strip_code_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return s


def plan_search(form: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate editable search queries, core terms, criteria, and credit estimate without calling search APIs."""
    question = str(form.get("question") or "").strip()
    if not question:
        raise ValueError("请先填写检索问题")
    cfg = model_config(config, task="plan")
    if not cfg["model"]:
        raise ValueError("无法预览检索方案：还没有填内置 Agent 的模型名。在「设置 → 内置 Agent」中填写")
    if not cfg["api_key"] and cfg["protocol"] == "anthropic":
        raise ValueError("无法预览检索方案：还没有填内置 Agent 的 API Key")

    mode = "deep" if form.get("mode") == "deep" else "quick"
    raw_criteria = str(form.get("criteria") or "").strip()
    system = (
        "你是学术文献检索规划助手。只输出一个合法 JSON 对象，不要输出任何额外说明或代码块以外的文字。"
        'JSON 字段固定为：{"queries": ["..."], "core_terms": ["..."], "criteria": ["..."]}。'
        "其中 queries 给 3–5 条空格分隔的纯英文关键词短语（每条 3–6 个词，如 \"pressureless infiltration SiC aluminum\"，不要写 AND/OR、括号或引号）；"
        "core_terms 给 2–4 条用于过滤候选池的英文核心概念词（每个概念一条，同义词用 | 连接，如 \"Al|aluminum|aluminium\"）；"
        "criteria 给 2–5 条简洁明确的中文纳入标准（如果用户已提供纳入标准，保留并微调即可）。"
    )
    user_parts = [f"检索问题：{question}", f"检索方式：{'深度检索' if mode == 'deep' else '快速查找'}"]
    if raw_criteria:
        user_parts.append(f"用户已填写的纳入标准：\n{raw_criteria}")
    if form.get("year_from") or form.get("year_to"):
        user_parts.append(f"年份范围：{form.get('year_from') or '不限'}–{form.get('year_to') or '至今'}")

    reply = chat([{"role": "user", "content": "\n".join(user_parts)}],
                 tools=None, config=config, system=system, max_tokens=1200, timeout=60, task="plan")
    raw_text = _strip_code_fence(strip_thinking(str(reply.get("text") or "")))
    try:
        parsed = json.loads(raw_text)
    except ValueError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    queries = [str(x).strip() for x in (parsed.get("queries") or []) if str(x).strip()][:6]
    if not queries:
        queries = [question]
    core_terms = [str(x).strip() for x in (parsed.get("core_terms") or []) if str(x).strip()][:6]
    criteria = [str(x).strip() for x in (parsed.get("criteria") or []) if str(x).strip()][:6]
    if not criteria and raw_criteria:
        criteria = [s.strip() for s in raw_criteria.splitlines() if s.strip()]

    est, note = estimate_search_credits(mode, len(queries), int(form.get("batches") or 0))
    usage = estimate_cost({cfg["model"]: normalize_usage(reply.get("usage"))}) if cfg.get("model") else estimate_cost({})
    return {
        "queries": queries,
        "core_terms": core_terms,
        "criteria": criteria,
        "estimated_credits": est,
        "credit_note": note,
        "model": cfg["model"],
        "usage": usage,
    }


def _accumulate_usage(acc: dict[str, dict[str, int]], model: str, raw_usage: dict[str, Any] | None) -> None:
    if not model:
        return
    norm = normalize_usage(raw_usage)
    cur = acc.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    cur["input_tokens"] += norm["input_tokens"]
    cur["output_tokens"] += norm["output_tokens"]
    cur["total_tokens"] += norm["total_tokens"]


def _merge_cost(acc: dict[str, dict[str, int]], cost_dict: dict[str, Any] | None) -> None:
    if not isinstance(cost_dict, dict):
        return
    for model, u in (cost_dict.get("by_model") or {}).items():
        _accumulate_usage(acc, model, u)


def run_task(goal: str, tools: list[dict[str, Any]], *, functions: dict[str, Tool] | None = None,
             config: dict[str, Any] | None = None, system: str = "", max_steps: int = 8,
             max_tokens: int = 4096, timeout: float = 180,
             on_event: EventHandler | None = None,
             task_for_step: Callable[[int, list[dict[str, Any]]], str] | None = None) -> dict[str, Any]:
    """Run until the model answers without calling a tool (or ``max_steps``)."""
    protocol = model_config(config)["protocol"]
    functions = functions or {}
    transcript: list[dict[str, Any]] = [{"role": "user", "text": goal}]
    produced: list[dict[str, Any]] = []
    usage_by_model: dict[str, dict[str, int]] = {}

    def emit(kind: str, **data: Any) -> None:
        if on_event:
            on_event(kind, data)

    for step in range(1, max_steps + 1):
        step_task = task_for_step(step, produced) if task_for_step else "default"
        step_model = model_config(config, task=step_task).get("model") or ""
        reply = chat(_messages_for(protocol, transcript), tools, config=config, system=system,
                     max_tokens=max_tokens, timeout=timeout, task=step_task)
        _accumulate_usage(usage_by_model, step_model, reply.get("usage"))
        text = (reply.get("text") or "").strip()
        calls = reply.get("tool_calls") or []
        if text:
            emit("text", text=text, step=step)
        if not calls:
            transcript.append({"role": "assistant", "text": text})
            return {"text": text, "steps": step, "tool_calls": produced,
                    "messages": _messages_for(protocol, transcript),
                    "usage": estimate_cost(usage_by_model)}
        for index, call in enumerate(calls):
            name = call.get("name") or ""
            # Some providers omit the id; a stable one keeps the next turn valid.
            call["id"] = call.get("id") or f"call_{step}_{index}"
            emit("tool", name=name, arguments=call.get("arguments") or {}, step=step)
            try:
                result = _call(functions, name, call.get("arguments") or {})
                call["result"] = result
            except ToolError as exc:
                call["result"] = f"出错：{exc}"
                emit("error", name=name, detail=str(exc), step=step)
            except Exception as exc:  # a tool blowing up must not end the task
                call["result"] = f"出错：{type(exc).__name__}: {exc}"
                emit("error", name=name, detail=str(exc), step=step)
            emit("result", name=name, result=call["result"], step=step)
            produced.append({"name": name, "arguments": call.get("arguments") or {},
                             "result": call["result"]})
        transcript.append({"role": "assistant", "text": text, "calls": calls})

    last = next((c["result"] for c in reversed(produced)), "")
    return {"text": f"到 {max_steps} 步还没有给出结论。最后一步工具返回：{last[:500]}",
            "steps": max_steps, "tool_calls": produced,
            "messages": _messages_for(protocol, transcript),
            "usage": estimate_cost(usage_by_model)}


def _as_str_list(val: Any) -> list[str]:
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [line.strip() for line in val.splitlines() if line.strip()]
    return []


def _opt_int(val: Any) -> int | None:
    if val in (None, "", False):
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _opt_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(val)


def _journal_criteria_lines(
    max_cas_zone: int | None,
    max_jcr_quartile: int | None,
    exclude_warning: bool,
) -> list[str]:
    lines: list[str] = []
    if max_cas_zone is not None:
        lines.append("优先中科院 1 区期刊" if max_cas_zone == 1 else f"优先中科院 1–{max_cas_zone} 区期刊")
    if max_jcr_quartile is not None:
        lines.append("优先 JCR Q1 期刊" if max_jcr_quartile == 1 else f"优先 JCR Q1–Q{max_jcr_quartile} 期刊")
    if exclude_warning:
        lines.append("排除预警期刊")
    return lines


def _select_seeds(
    question: str,
    criteria: list[str],
    candidates: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Pick 1-3 representative seed DOIs from candidates via the rating model, with top-3 fallback."""
    pool = [p for p in candidates if str(p.get("doi") or "").strip()]
    if not pool:
        return [], estimate_cost({})
    fallback = [str(p["doi"]).strip() for p in pool[:3]]

    system = (
        "你是学术文献检索助手。请从候选文献中挑选最贴合研究课题的 1–3 篇代表作作为深度检索的起点种子文献。"
        '只输出一个合法 JSON 对象，格式为 {"selected": [1, 4, 7]}，数字为候选文献的编号。不要输出任何其他文字。'
    )
    lines = [f"检索问题：{question}"]
    if criteria:
        lines.append("纳入标准：\n" + "\n".join(f"- {c}" for c in criteria))
    lines.append("候选文献：")
    for idx, p in enumerate(pool, 1):
        year = p.get("year") or "未知年份"
        title = str(p.get("title") or "").strip() or "无标题"
        journal = str(p.get("journal") or "").strip()
        doi = str(p.get("doi") or "").strip()
        header = f"{idx}. [{year}] {title}"
        if journal:
            header += f" — {journal}"
        header += f" (DOI: {doi})"
        lines.append(header)
        abstract = str(p.get("abstract") or "").strip()
        if abstract:
            lines.append(f"   摘要：{abstract[:300]}")

    cfg = model_config(config, task="rate")
    try:
        reply = chat(
            [{"role": "user", "content": "\n".join(lines)}],
            tools=None,
            config=config,
            system=system,
            max_tokens=256,
            timeout=60,
            task="rate",
        )
    except Exception:
        return fallback, estimate_cost({})

    usage = (
        estimate_cost({cfg["model"]: normalize_usage(reply.get("usage"))})
        if cfg.get("model")
        else estimate_cost({})
    )
    raw_text = _strip_code_fence(str(reply.get("text") or ""))
    try:
        parsed = json.loads(raw_text)
    except ValueError:
        parsed = {}

    raw_selected = parsed.get("selected") if isinstance(parsed, dict) else None
    picked: list[str] = []
    seen_dois: set[str] = set()
    if isinstance(raw_selected, list):
        for item in raw_selected:
            if isinstance(item, bool):
                continue
            try:
                num = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= num <= len(pool):
                doi = str(pool[num - 1].get("doi") or "").strip()
                if doi and doi.lower() not in seen_dois:
                    seen_dois.add(doi.lower())
                    picked.append(doi)
                    if len(picked) >= 3:
                        break
    return (picked or fallback), usage


def _format_summary(prefix: str, total_rated: int, by_rel: dict[str, int] | None, cancelled: bool) -> str:
    rel = by_rel or {}
    parts = [f"{k} {rel.get(k, 0)} 篇" for k in ("高", "中", "低", "不相关") if rel.get(k, 0)]
    detail = "（" + "，".join(parts) + "）" if parts else ""
    status = "已取消" if cancelled else "完成"
    return f"{prefix}{status}，累计评分 {total_rated} 篇{detail}。"


def run_builtin_search(
    form: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    stop_event: threading.Event | None = None,
    on_event: EventHandler | None = None,
) -> dict[str, Any]:
    """Run built-in quick or deep search using concurrent chunk scoring."""
    question = str(form.get("question") or "").strip()
    if not question:
        raise ValueError("请先填写检索问题")

    def emit(kind: str, data: dict[str, Any]) -> None:
        if on_event:
            on_event(kind, data)

    mode = "deep" if form.get("mode") == "deep" else "quick"
    criteria = _as_str_list(form.get("criteria"))
    queries = _as_str_list(form.get("planned_queries"))
    core_terms = _as_str_list(form.get("planned_core_terms"))
    steering_notes = _as_str_list(form.get("steering_notes"))
    year_from = form.get("year_from")
    year_to = form.get("year_to")
    sort = form.get("sort")
    max_cas_zone = _opt_int(form.get("max_cas_zone"))
    max_jcr_quartile = _opt_int(form.get("max_jcr_quartile"))
    exclude_warning = _opt_bool(form.get("exclude_warning"))
    usage_by_model: dict[str, dict[str, int]] = {}

    need_plan = (not queries) or (mode == "deep" and not core_terms)
    if need_plan:
        emit("progress", {"text": "正在规划检索词与纳入标准…"})
        plan = plan_search(form, config=config)
        _merge_cost(usage_by_model, plan.get("usage"))
        if not queries:
            queries = plan.get("queries") or [question]
        if not core_terms:
            core_terms = plan.get("core_terms") or []
        if not criteria:
            criteria = plan.get("criteria") or []

    if mode != "deep":
        limit = max(1, int(form.get("limit") or 20))
        per_query = max(10, min(limit, math.ceil(limit * 1.5 / max(1, len(queries))) + 4))
        runs: list[list[dict[str, Any]]] = []
        for q in queries:
            if stop_event and stop_event.is_set():
                return {
                    "text": "快速查找已取消。",
                    "session": "",
                    "usage": estimate_cost(usage_by_model),
                    "cancelled": True,
                }
            hits = quick.search_papers_v2(
                q,
                limit=per_query,
                year_from=year_from,
                year_to=year_to,
                sort=sort,
                max_cas_zone=max_cas_zone,
                max_jcr_quartile=max_jcr_quartile,
                exclude_warning=exclude_warning,
            )
            runs.append(hits)
        papers = quick._take_in_turns(runs)[:limit]

        if not papers:
            return {
                "text": "未检索到相关文献。",
                "session": "",
                "usage": estimate_cost(usage_by_model),
                "cancelled": bool(stop_event and stop_event.is_set()),
            }

        indexed_papers = [{**p, "n": idx} for idx, p in enumerate(papers, 1)]
        paper_by_n = {p["n"]: p for p in indexed_papers}
        session_id = ""
        last_saved: dict[str, Any] = {}
        query_str = " | ".join(queries)

        def on_quick_chunk(chunk_ratings: list[dict[str, Any]], done: int, total: int, _chunk_cost: dict[str, Any]) -> None:
            nonlocal session_id, last_saved
            items: list[dict[str, Any]] = []
            for r in chunk_ratings:
                src = paper_by_n.get(r["n"], {})
                item: dict[str, Any] = {
                    "doi": str(src.get("doi") or "").strip(),
                    "relevance": r["relevance"],
                    "reason": r.get("reason") or "",
                }
                for k in (
                    "title",
                    "year",
                    "journal",
                    "journal_rank",
                    "cited_by_count",
                    "abstract",
                    "abstract_missing",
                    "retracted",
                    "concern",
                    "is_oa",
                ):
                    if src.get(k) not in (None, "", []):
                        item[k] = src[k]
                items.append(item)
            from .. import __version__
            saved = screening.save(
                question,
                items,
                criteria=criteria,
                query=query_str,
                mode="quick",
                session=session_id or None,
                search_params={
                    "queries": list(queries),
                    "year_from": year_from,
                    "year_to": year_to,
                    "max_cas_zone": max_cas_zone,
                    "max_jcr_quartile": max_jcr_quartile,
                    "exclude_warning": exclude_warning,
                    "sources": ["openalex", "crossref", "semantic_scholar"],
                    "litloop_version": __version__,
                },
            )
            session_id = str(saved.get("session") or session_id)
            last_saved = saved
            emit("progress", {"text": f"正在评分 {done}/{total} 篇", "done": done, "total": total})
            emit("result", {"name": "screening_save", "result": saved})

        score_res = score_papers(
            question,
            criteria,
            indexed_papers,
            config=config,
            steering_notes=steering_notes or None,
            stop_event=stop_event,
            on_chunk_done=on_quick_chunk,
        )
        _merge_cost(usage_by_model, score_res.get("usage"))
        cancelled = bool(score_res.get("cancelled"))
        summary = _format_summary(
            "快速查找",
            int(last_saved.get("total") or len(score_res.get("ratings") or [])),
            last_saved.get("by_relevance"),
            cancelled,
        )
        return {
            "text": summary,
            "session": session_id,
            "usage": estimate_cost(usage_by_model),
            "cancelled": cancelled,
        }

    # Deep search mode
    for line in _journal_criteria_lines(max_cas_zone, max_jcr_quartile, exclude_warning):
        if line not in criteria:
            criteria.append(line)

    seeds = _as_str_list(form.get("seeds"))
    seed_collection = str(form.get("seed_collection") or "").strip()
    if seed_collection:
        emit("progress", {"text": f"正在从 Zotero 分类「{seed_collection}」读取种子文献…"})
        try:
            z_res = zotero_client.collection_dois(seed_collection, config or _config.load_config())
        except Exception as exc:
            emit("progress", {"text": f"读取 Zotero 分类「{seed_collection}」失败（{exc}），将继续后续流程。"})
        else:
            if not z_res.get("success"):
                err = z_res.get("error") or "未知错误"
                emit("progress", {"text": f"未能从 Zotero 分类「{seed_collection}」获取种子（{err}），将继续后续流程。"})
            else:
                z_dois = [str(d).strip() for d in (z_res.get("dois") or []) if str(d).strip()]
                if not z_dois:
                    emit("progress", {"text": f"Zotero 分类「{seed_collection}」内暂无带 DOI 的文献，将继续后续流程。"})
                else:
                    seen_seeds = {s.lower() for s in seeds}
                    for doi in z_dois:
                        if doi.lower() not in seen_seeds:
                            seen_seeds.add(doi.lower())
                            seeds.append(doi)

    if not seeds:
        emit("progress", {"text": "正在快速检索种子文献…"})
        seed_runs: list[list[dict[str, Any]]] = []
        for q in queries[:3]:
            if stop_event and stop_event.is_set():
                return {
                    "text": "深度检索已取消。",
                    "session": "",
                    "usage": estimate_cost(usage_by_model),
                    "cancelled": True,
                }
            seed_runs.append(
                quick.search_papers_v2(
                    q,
                    limit=10,
                    year_from=year_from,
                    year_to=year_to,
                    max_cas_zone=max_cas_zone,
                    max_jcr_quartile=max_jcr_quartile,
                    exclude_warning=exclude_warning,
                )
            )
        seed_candidates = [
            p for p in quick._take_in_turns(seed_runs) if str(p.get("doi") or "").strip()
        ][:15]
        if seed_candidates:
            emit("progress", {"text": f"正在从 {len(seed_candidates)} 篇候选中精选代表作作为种子…"})
            seeds, sel_usage = _select_seeds(question, criteria, seed_candidates, config=config)
            _merge_cost(usage_by_model, sel_usage)

    batch_size = max(1, int(form.get("batch_size") or 20))
    batches_limit = max(0, int(form.get("batches") or 0))
    must_find = _as_str_list(form.get("must_find")) or None

    emit("progress", {"text": "正在构建深度检索候选池…"})
    batch_res = deep.start(
        question,
        queries,
        seeds,
        core_terms,
        criteria=criteria or None,
        must_find=must_find,
        year_from=year_from,
        year_to=year_to,
        batch_size=batch_size,
    )
    session_id = str(batch_res.get("session") or "")
    emit("result", {"name": "deep_search_start", "result": batch_res})

    cancelled = bool(stop_event and stop_event.is_set())
    last_rate: dict[str, Any] = {}
    batch_idx = 1

    while not cancelled:
        batch_papers = batch_res.get("batch") or []
        if not batch_papers:
            break

        def on_deep_chunk(
            chunk_ratings: list[dict[str, Any]],
            done: int,
            total: int,
            _chunk_cost: dict[str, Any],
            *,
            _b: int = batch_idx,
        ) -> None:
            nonlocal last_rate
            rate_res = deep.rate(session_id, chunk_ratings)
            last_rate = rate_res
            emit("progress", {"text": f"第 {_b} 批：正在评分 {done}/{total} 篇", "done": done, "total": total, "batch": _b})
            emit("result", {"name": "deep_search_rate", "result": rate_res})

        score_res = score_papers(
            question,
            criteria,
            batch_papers,
            config=config,
            steering_notes=steering_notes or None,
            stop_event=stop_event,
            on_chunk_done=on_deep_chunk,
        )
        _merge_cost(usage_by_model, score_res.get("usage"))
        if score_res.get("cancelled") or (stop_event and stop_event.is_set()):
            cancelled = True
            break
        if batches_limit > 0 and batch_idx >= batches_limit:
            break
        if batch_res.get("stop_suggested"):
            break

        batch_idx += 1
        batch_res = deep.next_batch(session_id)
        emit("result", {"name": "deep_search_next", "result": batch_res})

    summary = _format_summary(
        "深度检索",
        int(last_rate.get("total") or batch_res.get("screened") or 0),
        last_rate.get("by_relevance"),
        cancelled,
    )
    return {
        "text": summary,
        "session": session_id,
        "usage": estimate_cost(usage_by_model),
        "cancelled": cancelled,
    }


def run_builtin_resume(
    session: str,
    *,
    batches: int = 0,
    steering_notes: list[str] | None = None,
    config: dict[str, Any] | None = None,
    stop_event: threading.Event | None = None,
    on_event: EventHandler | None = None,
) -> dict[str, Any]:
    """Resume an existing deep search session with user feedback few-shots and concurrent scoring."""
    def emit(kind: str, data: dict[str, Any]) -> None:
        if on_event:
            on_event(kind, data)

    st = deep.status(session)
    sc_data = screening.load(session)
    question = str(sc_data.get("question") or st.get("question") or session).strip()
    criteria = _as_str_list(sc_data.get("criteria"))
    few_shots = collect_user_feedback(session)
    usage_by_model: dict[str, dict[str, int]] = {}
    cancelled = bool(stop_event and stop_event.is_set())
    last_rate: dict[str, Any] = {}
    batches_limit = max(0, int(batches or 0))
    completed_batches = 0

    unrated = st.get("unrated") or []
    if unrated and not cancelled:
        def on_unrated_chunk(chunk_ratings: list[dict[str, Any]], done: int, total: int, _chunk_cost: dict[str, Any]) -> None:
            nonlocal last_rate
            rate_res = deep.rate(session, chunk_ratings)
            last_rate = rate_res
            emit("progress", {"text": f"续跑未评文献：正在评分 {done}/{total} 篇", "done": done, "total": total})
            emit("result", {"name": "deep_search_rate", "result": rate_res})

        score_res = score_papers(
            question,
            criteria,
            unrated,
            config=config,
            few_shots=few_shots or None,
            steering_notes=steering_notes,
            stop_event=stop_event,
            on_chunk_done=on_unrated_chunk,
        )
        _merge_cost(usage_by_model, score_res.get("usage"))
        cancelled = bool(score_res.get("cancelled") or (stop_event and stop_event.is_set()))

    stop_suggested = bool(st.get("stop_suggested"))
    while not cancelled and not stop_suggested:
        if batches_limit > 0 and completed_batches >= batches_limit:
            break
        batch_res = deep.next_batch(session)
        emit("result", {"name": "deep_search_next", "result": batch_res})
        batch_papers = batch_res.get("batch") or []
        stop_suggested = bool(batch_res.get("stop_suggested"))
        if not batch_papers:
            break

        completed_batches += 1

        def on_resume_chunk(
            chunk_ratings: list[dict[str, Any]],
            done: int,
            total: int,
            _chunk_cost: dict[str, Any],
            *,
            _b: int = completed_batches,
        ) -> None:
            nonlocal last_rate
            rate_res = deep.rate(session, chunk_ratings)
            last_rate = rate_res
            emit("progress", {"text": f"续跑第 {_b} 批：正在评分 {done}/{total} 篇", "done": done, "total": total, "batch": _b})
            emit("result", {"name": "deep_search_rate", "result": rate_res})

        score_res = score_papers(
            question,
            criteria,
            batch_papers,
            config=config,
            few_shots=few_shots or None,
            steering_notes=steering_notes,
            stop_event=stop_event,
            on_chunk_done=on_resume_chunk,
        )
        _merge_cost(usage_by_model, score_res.get("usage"))
        if score_res.get("cancelled") or (stop_event and stop_event.is_set()):
            cancelled = True
            break

    summary = _format_summary(
        "深度检索续跑",
        int(last_rate.get("total") or st.get("screened") or 0),
        last_rate.get("by_relevance"),
        cancelled,
    )
    return {
        "text": summary,
        "session": session,
        "usage": estimate_cost(usage_by_model),
        "cancelled": cancelled,
    }
