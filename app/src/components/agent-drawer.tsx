import * as React from "react"
import {
  BotIcon,
  CheckCircle2Icon,
  LoaderIcon,
  PlusIcon,
  SendIcon,
  SparklesIcon,
  SquareIcon,
  Trash2Icon,
  XCircleIcon,
  XIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { type Paper } from "@/components/detail-pane"
import {
  agentChatApi,
  errorMessage,
  rating,
  type AgentActionCard,
  type AgentMessage,
  type AgentViewContext,
  type RunUsage,
  type SteeringNote,
} from "@/lib/api"
import { cn } from "@/lib/utils"

const QUICK_PROMPTS = [
  "总结当前批次高相关文献的共性",
  "把下一批重点转向实验验证类论文",
  "帮我对比当前这篇文献与课题标准的匹配度",
]

const STATUS_META: Record<AgentActionCard["status"], { label: string; className: string }> = {
  pending: { label: "待确认", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  confirmed: { label: "已确认", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" },
  cancelled: { label: "已取消", className: "bg-muted text-muted-foreground" },
  failed: { label: "执行失败", className: "bg-destructive/15 text-destructive" },
}

function formatUsage(usage?: RunUsage | null): string | null {
  if (!usage) return null
  const parts: string[] = []
  if (usage.total_tokens > 0) {
    parts.push(`${usage.total_tokens.toLocaleString()} tokens`)
  }
  if (usage.cost_text) {
    parts.push(usage.cost_text)
  }
  return parts.length ? parts.join(" · ") : null
}

function renderInlineMarkdown(
  text: string,
  onSelectPaperByNumber?: (n: number) => void,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\)|\[#\d+\])/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let keyIdx = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    const token = match[0]
    const k = `im-${keyIdx++}`
    if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(
        <code key={k} className="rounded bg-background/70 px-1 py-0.5 font-mono text-[12px]">
          {token.slice(1, -1)}
        </code>,
      )
    } else if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(<strong key={k} className="font-semibold">{token.slice(2, -2)}</strong>)
    } else if (token.startsWith("*") && token.endsWith("*")) {
      nodes.push(<em key={k}>{token.slice(1, -1)}</em>)
    } else if (/^\[#\d+\]$/.test(token)) {
      const num = Number(token.slice(2, -1))
      if (onSelectPaperByNumber && Number.isFinite(num)) {
        nodes.push(
          <button
            key={k}
            type="button"
            onClick={() => onSelectPaperByNumber(num)}
            title={`跳转到文献 #${num}`}
            className="mx-0.5 inline-flex items-center rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] font-medium text-primary transition-colors hover:bg-primary/20"
          >
            {token}
          </button>,
        )
      } else {
        nodes.push(
          <span
            key={k}
            className="mx-0.5 inline-flex items-center rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] font-medium text-primary"
          >
            {token}
          </span>,
        )
      }
    } else if (token.startsWith("[")) {
      const m = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      if (m && /^https?:\/\//i.test(m[2].trim())) {
        nodes.push(
          <a
            key={k}
            href={m[2].trim()}
            target="_blank"
            rel="noreferrer"
            className="text-primary underline underline-offset-2"
          >
            {m[1]}
          </a>,
        )
      } else {
        nodes.push(token)
      }
    } else {
      nodes.push(token)
    }
    lastIndex = pattern.lastIndex
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }
  return nodes
}

function splitTableRow(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim())
}

function SimpleMarkdown({
  content,
  onSelectPaperByNumber,
}: {
  content: string
  onSelectPaperByNumber?: (n: number) => void
}) {
  const lines = content.replace(/\r\n/g, "\n").split("\n")
  const blocks: React.ReactNode[] = []
  let i = 0
  let bIdx = 0

  while (i < lines.length) {
    const line = lines[i]
    if (line.trim().startsWith("```")) {
      const codeLines: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i])
        i += 1
      }
      if (i < lines.length) i += 1
      blocks.push(
        <pre key={`b-${bIdx++}`} className="my-1.5 overflow-x-auto rounded-md border bg-background/80 p-2 font-mono text-[11px] leading-relaxed">
          <code>{codeLines.join("\n")}</code>
        </pre>,
      )
      continue
    }

    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-{3,}:?(\s*\|\s*:?-{3,}:?)*\s*\|?\s*$/.test(lines[i + 1])
    ) {
      const headers = splitTableRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(splitTableRow(lines[i]))
        i += 1
      }
      blocks.push(
        <div key={`b-${bIdx++}`} className="my-1.5 overflow-x-auto rounded-md border bg-background/70">
          <table className="w-full border-collapse text-left text-[12px]">
            <thead>
              <tr className="border-b bg-muted/40">
                {headers.map((h, hi) => (
                  <th key={hi} className="px-2 py-1 font-medium">{renderInlineMarkdown(h, onSelectPaperByNumber)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="border-b last:border-b-0">
                  {headers.map((_, ci) => (
                    <td key={ci} className="px-2 py-1 align-top">{renderInlineMarkdown(row[ci] ?? "", onSelectPaperByNumber)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line.trim())
    if (heading) {
      blocks.push(
        <p key={`b-${bIdx++}`} className="mt-1.5 mb-0.5 font-semibold text-foreground">
          {renderInlineMarkdown(heading[2], onSelectPaperByNumber)}
        </p>,
      )
      i += 1
      continue
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""))
        i += 1
      }
      blocks.push(
        <ul key={`b-${bIdx++}`} className="my-1 list-disc space-y-0.5 pl-4">
          {items.map((it, ii) => <li key={ii}>{renderInlineMarkdown(it, onSelectPaperByNumber)}</li>)}
        </ul>,
      )
      continue
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""))
        i += 1
      }
      blocks.push(
        <ol key={`b-${bIdx++}`} className="my-1 list-decimal space-y-0.5 pl-4">
          {items.map((it, ii) => <li key={ii}>{renderInlineMarkdown(it, onSelectPaperByNumber)}</li>)}
        </ol>,
      )
      continue
    }

    if (!line.trim()) {
      i += 1
      continue
    }

    const paraLines: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].trim().startsWith("```") &&
      !/^#{1,3}\s+/.test(lines[i].trim()) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      paraLines.push(lines[i])
      i += 1
    }
    blocks.push(
      <p key={`b-${bIdx++}`} className="my-1 first:mt-0 last:mb-0">
        {paraLines.map((pl, pi) => (
          <React.Fragment key={pi}>
            {pi > 0 && <br />}
            {renderInlineMarkdown(pl, onSelectPaperByNumber)}
          </React.Fragment>
        ))}
      </p>,
    )
  }

  return <div className="space-y-1 break-words">{blocks}</div>
}

function buildViewContext(paper: Paper | null): AgentViewContext | undefined {
  if (!paper) return undefined
  const item = paper.item
  return {
    current_paper: {
      doi: paper.doi ?? null,
      title: paper.title,
      year: paper.year ?? null,
      journal: paper.journal ?? null,
      relevance: item ? item.relevance : null,
      user_relevance: item?.user_relevance ?? null,
      reason: item?.reason ?? null,
      abstract: paper.abstract ?? null,
    },
  }
}

function glossaryTextFromParams(params: Record<string, unknown>): string {
  if (Array.isArray(params.terms)) {
    return params.terms.map((t) => (typeof t === "string" ? t : JSON.stringify(t))).join("，")
  }
  if (Array.isArray(params.with)) {
    return params.with.map((t) => String(t)).join("，")
  }
  if (typeof params.term === "string") return params.term
  return ""
}

interface BatchScreenItem {
  n: number
  doi?: string
  title?: string
  cur_relevance?: string
  cur_decision?: "selected" | "rejected" | null
  relevance?: string
  decision?: "selected" | "rejected" | null
  reject_reason?: string
  note?: string
}

function decisionLabel(d?: string | null): string {
  if (d === "selected") return "入选"
  if (d === "rejected") return "排除"
  return "未定"
}

function ReasoningBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <details className="mb-1.5 max-w-[92%] rounded-lg border bg-muted/35 px-2.5 py-1.5 text-xs text-muted-foreground">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 select-none font-medium text-muted-foreground hover:text-foreground">
        {streaming && <LoaderIcon className="size-3 shrink-0 animate-spin" />}
        <span>思考过程（可选展开）</span>
      </summary>
      <div className="mt-1.5 max-h-56 overflow-y-auto whitespace-pre-wrap border-t pt-1.5 text-[11px] leading-relaxed">
        {text}
      </div>
    </details>
  )
}

function ActionCardView({
  sessionId,
  messageId,
  action,
  onUpdated,
  onConfirmed,
}: {
  sessionId: string
  messageId: number
  action: AgentActionCard
  onUpdated: (messageId: number, nextAction: AgentActionCard) => void
  onConfirmed?: (action: AgentActionCard, result?: Record<string, unknown>) => void
}) {
  const batchItems = React.useMemo<BatchScreenItem[]>(
    () => (action.kind === "batch_screen" && Array.isArray(action.params.items)
      ? (action.params.items as BatchScreenItem[])
      : []),
    [action.kind, action.params.items],
  )
  const [params, setParams] = React.useState<Record<string, unknown>>(() => ({ ...action.params }))
  const [glossaryInput, setGlossaryInput] = React.useState(() => glossaryTextFromParams(action.params))
  const [selectedNos, setSelectedNos] = React.useState<Set<number>>(() => new Set(batchItems.map((it) => it.n)))
  const [busy, setBusy] = React.useState<"confirm" | "cancel" | null>(null)
  const [cardError, setCardError] = React.useState<string | null>(null)

  React.useEffect(() => {
    setParams({ ...action.params })
    setGlossaryInput(glossaryTextFromParams(action.params))
    setSelectedNos(new Set(batchItems.map((it) => it.n)))
  }, [action.id, action.params, batchItems])

  const isPending = action.status === "pending"
  const canAct = messageId > 0
  const meta = STATUS_META[action.status]

  const setField = (key: string, value: unknown) => {
    setParams((prev) => ({ ...prev, [key]: value }))
  }

  const buildSubmitParams = (): Record<string, unknown> => {
    if (action.kind === "batch_screen") {
      return { ...params, items: batchItems.filter((it) => selectedNos.has(it.n)) }
    }
    if (action.kind !== "add_glossary") return params
    const items = glossaryInput
      .split(/[,，\n]+/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (Array.isArray(action.params.with)) {
      return { ...params, with: items }
    }
    if (Array.isArray(action.params.terms) || items.length > 1) {
      return { ...params, terms: items }
    }
    return { ...params, term: items[0] ?? "" }
  }

  const handleConfirm = async () => {
    if (!canAct || busy) return
    setBusy("confirm")
    setCardError(null)
    try {
      const submitParams = buildSubmitParams()
      const res = await agentChatApi.confirmAction(sessionId, messageId, action.id, submitParams)
      onUpdated(messageId, res.action)
      if (res.ok) {
        onConfirmed?.(res.action, res.result ?? undefined)
      } else if (res.error) {
        setCardError(res.error)
      }
    } catch (e) {
      setCardError(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const handleCancel = async () => {
    if (!canAct || busy) return
    setBusy("cancel")
    setCardError(null)
    try {
      const res = await agentChatApi.cancelAction(sessionId, messageId, action.id)
      onUpdated(messageId, res.action)
    } catch (e) {
      setCardError(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const downloadCount = Array.isArray(params.dois)
    ? params.dois.length
    : Array.isArray(params.papers)
      ? params.papers.length
      : typeof params.count === "number"
        ? params.count
        : null

  return (
    <div className="mt-2 rounded-lg border bg-card p-3 text-card-foreground shadow-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-medium leading-snug">{action.title}</div>
          {action.summary && (
            <p className="mt-0.5 text-xs text-muted-foreground leading-relaxed">{action.summary}</p>
          )}
        </div>
        <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", meta.className)}>
          {meta.label}
        </span>
      </div>

      {isPending && (
        <div className="mt-2.5 space-y-2 border-t pt-2.5 text-xs">
          {action.kind === "start_search" && (
            <>
              <label className="block space-y-1">
                <span className="text-muted-foreground">检索课题</span>
                <textarea
                  rows={2}
                  value={String(params.question ?? "")}
                  onChange={(e) => setField("question", e.target.value)}
                  className="w-full resize-none rounded-md border bg-background px-2.5 py-1.5 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring"
                />
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">批次数</span>
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={Number(params.batches ?? 1)}
                  onChange={(e) => setField("batches", Math.max(1, Number(e.target.value) || 1))}
                  className="h-7 w-24 text-xs"
                />
              </label>
            </>
          )}

          {action.kind === "resume_search" && (
            <>
              <label className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">追加批次数</span>
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={Number(params.batches ?? 1)}
                  onChange={(e) => setField("batches", Math.max(1, Number(e.target.value) || 1))}
                  className="h-7 w-24 text-xs"
                />
              </label>
              <label className="block space-y-1">
                <span className="text-muted-foreground">纠偏备注</span>
                <Input
                  value={String(params.steering_note ?? "")}
                  onChange={(e) => setField("steering_note", e.target.value)}
                  placeholder="可选，例如侧重实验验证"
                  className="h-7 text-xs"
                />
              </label>
            </>
          )}

          {action.kind === "add_glossary" && (
            <div className="space-y-1.5">
              {typeof params.term === "string" && Array.isArray(params.with) && (
                <label className="block space-y-1">
                  <span className="text-muted-foreground">主词条</span>
                  <Input
                    value={String(params.term ?? "")}
                    onChange={(e) => setField("term", e.target.value)}
                    className="h-7 text-xs"
                  />
                </label>
              )}
              <label className="block space-y-1">
                <span className="text-muted-foreground">
                  {Array.isArray(params.with) ? "同义/关联词（逗号分隔）" : "术语列表（逗号分隔）"}
                </span>
                <Input
                  value={glossaryInput}
                  onChange={(e) => setGlossaryInput(e.target.value)}
                  placeholder="输入术语，多个请用逗号分隔"
                  className="h-7 text-xs"
                />
              </label>
            </div>
          )}

          {action.kind === "zotero_push" && (
            <label className="block space-y-1">
              <span className="text-muted-foreground">目标分类</span>
              <Input
                value={String(params.collection ?? "")}
                onChange={(e) => setField("collection", e.target.value)}
                placeholder="输入 Zotero 分类名或路径"
                className="h-7 text-xs"
              />
            </label>
          )}

          {action.kind === "download" && (
            <div className="space-y-1.5">
              {downloadCount !== null && (
                <div className="flex items-center justify-between text-muted-foreground">
                  <span>待下载文献</span>
                  <span className="font-medium text-foreground">{downloadCount} 篇</span>
                </div>
              )}
              {typeof params.skipped_downloaded === "number" && params.skipped_downloaded > 0 && (
                <div className="flex items-center justify-between text-muted-foreground">
                  <span>跳过本机已有</span>
                  <span className="font-medium text-foreground">{params.skipped_downloaded} 篇</span>
                </div>
              )}
              {Array.isArray(params.preview_titles) && params.preview_titles.length > 0 && (
                <div className="space-y-1 rounded-md border bg-background/60 p-2">
                  <div className="text-[11px] text-muted-foreground">文献预览（前 {Math.min(5, params.preview_titles.length)} 篇）</div>
                  <ul className="space-y-0.5 text-[11px] text-foreground">
                    {params.preview_titles.slice(0, 5).map((t, ti) => (
                      <li key={ti} className="truncate" title={String(t)}>
                        · {String(t)}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <label className="block space-y-1">
                <span className="text-muted-foreground">任务名称</span>
                <Input
                  value={String(params.title ?? "")}
                  onChange={(e) => setField("title", e.target.value)}
                  placeholder="下载任务名称"
                  className="h-7 text-xs"
                />
              </label>
            </div>
          )}

          {action.kind === "retry_download" && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-muted-foreground">
                <span>目标任务</span>
                <span className="font-mono font-medium text-foreground">{String(params.job ?? "")}</span>
              </div>
              <div className="flex items-center justify-between text-muted-foreground">
                <span>重试模式</span>
                <span className="font-medium text-foreground">
                  {params.mode === "publisher_queue"
                    ? `启动出版商队列：${String(params.publisher ?? "")}`
                    : "失败文献重试"}
                </span>
              </div>
              {typeof params.count === "number" && (
                <div className="flex items-center justify-between text-muted-foreground">
                  <span>涉及文献</span>
                  <span className="font-medium text-foreground">{params.count} 篇</span>
                </div>
              )}
            </div>
          )}

          {action.kind === "batch_screen" && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span>已选 {selectedNos.size}/{batchItems.length} 篇</span>
                <button
                  type="button"
                  disabled={!canAct || busy !== null || batchItems.length === 0}
                  onClick={() => {
                    if (selectedNos.size === batchItems.length) {
                      setSelectedNos(new Set())
                    } else {
                      setSelectedNos(new Set(batchItems.map((it) => it.n)))
                    }
                  }}
                  className="text-primary hover:underline disabled:opacity-50"
                >
                  {selectedNos.size === batchItems.length ? "取消全选" : "全选"}
                </button>
              </div>
              <div className="max-h-52 space-y-1.5 overflow-y-auto pr-0.5">
                {batchItems.map((it) => {
                  const checked = selectedNos.has(it.n)
                  const targetRel = it.relevance ?? it.cur_relevance
                  const targetDec = it.decision !== undefined ? it.decision : it.cur_decision
                  const reasonText = it.reject_reason || it.note
                  return (
                    <label
                      key={it.n}
                      className="flex cursor-pointer items-start gap-2 rounded-md border bg-background/70 p-2 text-[11px] hover:bg-accent/40"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!canAct || busy !== null}
                        onChange={(e) => {
                          setSelectedNos((prev) => {
                            const next = new Set(prev)
                            if (e.target.checked) next.add(it.n)
                            else next.delete(it.n)
                            return next
                          })
                        }}
                        className="mt-0.5 size-3.5 shrink-0 accent-primary"
                      />
                      <div className="min-w-0 flex-1 space-y-0.5">
                        <div className="truncate font-medium text-foreground" title={it.title || it.doi || `文献 #${it.n}`}>
                          <span className="mr-1 font-mono text-muted-foreground">[#{it.n}]</span>
                          {it.title || it.doi || `文献 #${it.n}`}
                        </div>
                        <div className="flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                          <span>
                            {it.cur_relevance || "未评"}
                            {it.cur_decision ? ` (${decisionLabel(it.cur_decision)})` : ""}
                          </span>
                          <span>→</span>
                          <span className="rounded bg-primary/10 px-1 py-0.5 font-medium text-primary">
                            {targetRel || "未评"}
                            {targetDec ? ` · ${decisionLabel(targetDec)}` : ""}
                          </span>
                        </div>
                        {reasonText && (
                          <div className="line-clamp-2 text-[10px] text-muted-foreground">{reasonText}</div>
                        )}
                      </div>
                    </label>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {cardError && (
        <p className="mt-2 rounded bg-destructive/10 px-2 py-1 text-[11px] text-destructive">{cardError}</p>
      )}

      {isPending && (
        <div className="mt-3 flex items-center justify-between gap-2">
          {!canAct ? (
            <span className="text-[11px] text-muted-foreground">生成中，完成后可操作</span>
          ) : (
            <span />
          )}
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2.5 text-xs"
              disabled={!canAct || busy !== null}
              onClick={() => void handleCancel()}
            >
              {busy === "cancel" ? <LoaderIcon className="size-3 animate-spin" /> : "取消"}
            </Button>
            <Button
              size="sm"
              className="h-7 px-3 text-xs"
              disabled={!canAct || busy !== null}
              onClick={() => void handleConfirm()}
            >
              {busy === "confirm" ? <LoaderIcon className="size-3 animate-spin" /> : "确认执行"}
            </Button>
          </div>
        </div>
      )}

      {action.status === "confirmed" && action.result && typeof action.result.message === "string" && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-emerald-700 dark:text-emerald-400">
          <CheckCircle2Icon className="size-3.5 shrink-0" />
          <span className="truncate">{action.result.message}</span>
        </div>
      )}
      {action.status === "failed" && action.result && typeof action.result.error === "string" && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-destructive">
          <XCircleIcon className="size-3.5 shrink-0" />
          <span className="truncate">{action.result.error}</span>
        </div>
      )}
    </div>
  )
}

export interface AgentDrawerProps {
  sessionId: string
  sessionTitle: string | null
  currentPaper: Paper | null
  onClose: () => void
  onActionConfirmed?: (action: AgentActionCard, result?: Record<string, unknown>) => void
  onSelectPaperByNumber?: (n: number) => void
}

export function AgentDrawer({
  sessionId,
  sessionTitle,
  currentPaper,
  onClose,
  onActionConfirmed,
  onSelectPaperByNumber,
}: AgentDrawerProps) {
  const [messages, setMessages] = React.useState<AgentMessage[]>([])
  const [steeringNotes, setSteeringNotes] = React.useState<SteeringNote[]>([])
  const [loading, setLoading] = React.useState(false)
  const [loadError, setLoadError] = React.useState<string | null>(null)
  const [input, setInput] = React.useState("")
  const [streaming, setStreaming] = React.useState(false)
  const [streamText, setStreamText] = React.useState("")
  const [streamReasoning, setStreamReasoning] = React.useState("")
  const [reasoningMap, setReasoningMap] = React.useState<Record<number, string>>({})
  const [streamActions, setStreamActions] = React.useState<AgentActionCard[]>([])
  const [toolLabel, setToolLabel] = React.useState<string | null>(null)
  const [streamError, setStreamError] = React.useState<string | null>(null)
  const [addingNote, setAddingNote] = React.useState(false)
  const [noteInput, setNoteInput] = React.useState("")
  const [noteBusy, setNoteBusy] = React.useState(false)

  const abortRef = React.useRef<AbortController | null>(null)
  const stoppedByUserRef = React.useRef(false)
  const streamTextRef = React.useRef("")
  const streamReasoningRef = React.useRef("")
  const streamActionsRef = React.useRef<AgentActionCard[]>([])
  const scrollRef = React.useRef<HTMLDivElement | null>(null)
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null)

  React.useEffect(() => {
    stoppedByUserRef.current = false
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
    streamTextRef.current = ""
    streamReasoningRef.current = ""
    streamActionsRef.current = []
    setStreamText("")
    setStreamReasoning("")
    setReasoningMap({})
    setStreamActions([])
    setToolLabel(null)
    setStreamError(null)
    setLoading(true)
    setLoadError(null)

    let cancelled = false
    agentChatApi
      .load(sessionId)
      .then((res) => {
        if (cancelled) return
        setMessages(res.messages)
        setSteeringNotes(res.steering_notes.filter((n) => n.active))
      })
      .catch((e) => {
        if (cancelled) return
        setLoadError(errorMessage(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [sessionId])

  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, streamText, streamReasoning, streamActions, toolLabel])

  const sendMessage = React.useCallback(
    async (rawText?: string) => {
      const text = (rawText ?? input).trim()
      if (!text || streaming) return

      if (!rawText) setInput("")
      stoppedByUserRef.current = false
      streamTextRef.current = ""
      streamReasoningRef.current = ""
      streamActionsRef.current = []
      setStreamError(null)
      setStreamText("")
      setStreamReasoning("")
      setStreamActions([])
      setToolLabel(null)
      setStreaming(true)

      const tempId = -Date.now()
      const optimisticUser: AgentMessage = {
        id: tempId,
        session_id: sessionId,
        role: "user",
        content: text,
        actions: [],
        created: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, optimisticUser])

      const controller = new AbortController()
      abortRef.current = controller

      try {
        await agentChatApi.stream(
          sessionId,
          text,
          buildViewContext(currentPaper),
          (ev) => {
            if (ev.type === "user_message") {
              setMessages((prev) => prev.map((m) => (m.id === tempId ? ev.message : m)))
            } else if (ev.type === "reasoning") {
              setToolLabel(null)
              streamReasoningRef.current += ev.text
              setStreamReasoning(streamReasoningRef.current)
            } else if (ev.type === "delta") {
              setToolLabel(null)
              streamTextRef.current += ev.text
              setStreamText(streamTextRef.current)
            } else if (ev.type === "tool") {
              setToolLabel(ev.label || `正在调用 ${ev.name}…`)
            } else if (ev.type === "action") {
              streamActionsRef.current = [...streamActionsRef.current, ev.action]
              setStreamActions(streamActionsRef.current)
            } else if (ev.type === "done") {
              const savedReasoning = streamReasoningRef.current.trim()
              if (savedReasoning) {
                setReasoningMap((prev) => ({ ...prev, [ev.message.id]: streamReasoningRef.current }))
              }
              setMessages((prev) => [...prev, ev.message])
              streamTextRef.current = ""
              streamReasoningRef.current = ""
              streamActionsRef.current = []
              setStreamText("")
              setStreamReasoning("")
              setStreamActions([])
              setToolLabel(null)
            } else if (ev.type === "error") {
              setStreamError(ev.error)
            }
          },
          controller.signal,
        )
      } catch (e) {
        if ((e as Error)?.name === "AbortError") {
          if (stoppedByUserRef.current) {
            const partialText = streamTextRef.current
            const partialReasoning = streamReasoningRef.current
            const partialActions = streamActionsRef.current
            const partialId = -Date.now() - 1
            const content = partialText.trim() ? `${partialText}\n\n（已停止生成）` : "（已停止生成）"
            setMessages((prev) => [
              ...prev,
              {
                id: partialId,
                session_id: sessionId,
                role: "assistant",
                content,
                actions: partialActions,
                created: new Date().toISOString(),
              },
            ])
            if (partialReasoning.trim()) {
              setReasoningMap((prev) => ({ ...prev, [partialId]: partialReasoning }))
            }
            streamTextRef.current = ""
            streamReasoningRef.current = ""
            streamActionsRef.current = []
            setStreamText("")
            setStreamReasoning("")
            setStreamActions([])
          }
        } else {
          setStreamError(errorMessage(e))
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null
        }
        stoppedByUserRef.current = false
        setStreaming(false)
        setToolLabel(null)
      }
    },
    [input, streaming, sessionId, currentPaper],
  )

  const handleStop = () => {
    stoppedByUserRef.current = true
    abortRef.current?.abort()
  }

  const handleClear = async () => {
    if (streaming || messages.length === 0) return
    try {
      await agentChatApi.clear(sessionId)
      setMessages([])
      setReasoningMap({})
      setStreamError(null)
    } catch (e) {
      setStreamError(errorMessage(e))
    }
  }

  const handleAddNote = async () => {
    const note = noteInput.trim()
    if (!note || noteBusy) return
    setNoteBusy(true)
    try {
      const created = await agentChatApi.addSteering(sessionId, note)
      setSteeringNotes((prev) => [...prev, created])
      setNoteInput("")
      setAddingNote(false)
    } catch (e) {
      setStreamError(errorMessage(e))
    } finally {
      setNoteBusy(false)
    }
  }

  const handleRemoveNote = async (noteId: number) => {
    try {
      await agentChatApi.removeSteering(noteId)
      setSteeringNotes((prev) => prev.filter((n) => n.id !== noteId))
    } catch (e) {
      setStreamError(errorMessage(e))
    }
  }

  const handleActionUpdated = React.useCallback((messageId: number, nextAction: AgentActionCard) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId
          ? { ...m, actions: m.actions.map((a) => (a.id === nextAction.id ? nextAction : a)) }
          : m,
      ),
    )
  }, [])

  const scopeLabel = sessionId === "__draft__" || !sessionTitle ? "新检索草稿" : sessionTitle

  return (
    <aside
      aria-label="内置 Agent 助手"
      className="flex min-h-0 w-[380px] shrink-0 flex-col border-l bg-background"
    >
      {/* 顶部栏 */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3.5 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <BotIcon className="size-4 shrink-0 text-primary" />
          <span className="shrink-0 text-sm font-medium">内置 Agent 助手</span>
          <Badge variant="secondary" className="max-w-[130px] truncate text-[11px] font-normal" title={scopeLabel}>
            {scopeLabel}
          </Badge>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            className="size-7 p-0 text-muted-foreground hover:text-foreground"
            disabled={streaming || messages.length === 0}
            onClick={() => void handleClear()}
            title="清空对话"
            aria-label="清空对话"
          >
            <Trash2Icon className="size-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="size-7 p-0 text-muted-foreground hover:text-foreground"
            onClick={onClose}
            title="收起助手（Ctrl+L）"
            aria-label="收起助手"
          >
            <XIcon className="size-4" />
          </Button>
        </div>
      </div>

      {/* 纠偏备注条 */}
      <div className="shrink-0 border-b bg-muted/25 px-3.5 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-medium text-muted-foreground">纠偏备注</span>
          {!addingNote && (
            <button
              type="button"
              onClick={() => setAddingNote(true)}
              className="flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground"
            >
              <PlusIcon className="size-3" />添加
            </button>
          )}
        </div>
        {steeringNotes.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {steeringNotes.map((n) => (
              <span
                key={n.id}
                className="inline-flex max-w-full items-center gap-1 rounded-md border bg-background px-2 py-0.5 text-[11px]"
              >
                <span className="truncate">{n.note}</span>
                <button
                  type="button"
                  aria-label="删除纠偏备注"
                  onClick={() => void handleRemoveNote(n.id)}
                  className="shrink-0 text-muted-foreground hover:text-foreground"
                >
                  <XIcon className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        {addingNote && (
          <div className="mt-1.5 flex items-center gap-1.5">
            <Input
              autoFocus
              value={noteInput}
              onChange={(e) => setNoteInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  void handleAddNote()
                } else if (e.key === "Escape") {
                  setAddingNote(false)
                  setNoteInput("")
                }
              }}
              placeholder="输入后续检索或筛选偏好…"
              className="h-7 text-xs"
            />
            <Button size="sm" className="h-7 px-2.5 text-xs" disabled={noteBusy} onClick={() => void handleAddNote()}>
              确定
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={() => {
                setAddingNote(false)
                setNoteInput("")
              }}
            >
              取消
            </Button>
          </div>
        )}
      </div>

      {/* 消息流 */}
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
        {loading && (
          <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">
            <LoaderIcon className="mr-1.5 size-3.5 animate-spin" />正在加载对话…
          </div>
        )}

        {loadError && (
          <div className="rounded-md bg-destructive/10 p-2.5 text-xs text-destructive">{loadError}</div>
        )}

        {!loading && !loadError && messages.length === 0 && !streaming && (
          <div className="space-y-3 py-4">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <SparklesIcon className="size-3.5 text-primary" />
              <span>你可以随时针对当前课题、批次或选中文献提问：</span>
            </div>
            <div className="flex flex-col gap-1.5">
              {QUICK_PROMPTS.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => void sendMessage(q)}
                  className="rounded-lg border bg-card px-3 py-2 text-left text-xs text-foreground transition-colors hover:bg-accent"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => {
          const isUser = m.role === "user"
          const usageText = !isUser ? formatUsage(m.usage) : null
          const reasoningText = !isUser ? reasoningMap[m.id] : undefined
          return (
            <div key={m.id} className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
              {reasoningText && <ReasoningBlock text={reasoningText} />}
              <div
                className={cn(
                  "max-w-[92%] break-words rounded-xl px-3 py-2 text-[13px] leading-relaxed",
                  isUser ? "whitespace-pre-wrap bg-primary text-primary-foreground" : "bg-muted/65 text-foreground",
                )}
              >
                {isUser ? m.content : <SimpleMarkdown content={m.content} onSelectPaperByNumber={onSelectPaperByNumber} />}
              </div>
              {!isUser && m.actions?.length > 0 && (
                <div className="w-full max-w-[96%]">
                  {m.actions.map((act) => (
                    <ActionCardView
                      key={act.id}
                      sessionId={sessionId}
                      messageId={m.id}
                      action={act}
                      onUpdated={handleActionUpdated}
                      onConfirmed={onActionConfirmed}
                    />
                  ))}
                </div>
              )}
              {usageText && (
                <span className="mt-1 px-1 text-[11px] text-muted-foreground">{usageText}</span>
              )}
            </div>
          )
        })}

        {streaming && (
          <div className="flex flex-col items-start">
            {toolLabel && (
              <div className="mb-1.5 flex items-center gap-1.5 rounded-md bg-muted/50 px-2.5 py-1 text-xs text-muted-foreground">
                <LoaderIcon className="size-3 animate-spin" />
                <span>{toolLabel}</span>
              </div>
            )}
            {streamReasoning && <ReasoningBlock text={streamReasoning} streaming={!streamText} />}
            {streamText && (
              <div className="max-w-[92%] break-words rounded-xl bg-muted/65 px-3 py-2 text-[13px] leading-relaxed text-foreground">
                <SimpleMarkdown content={streamText} onSelectPaperByNumber={onSelectPaperByNumber} />
              </div>
            )}
            {streamActions.length > 0 && (
              <div className="w-full max-w-[96%]">
                {streamActions.map((act) => (
                  <ActionCardView
                    key={act.id}
                    sessionId={sessionId}
                    messageId={0}
                    action={act}
                    onUpdated={handleActionUpdated}
                    onConfirmed={onActionConfirmed}
                  />
                ))}
              </div>
            )}
            {!toolLabel && !streamReasoning && !streamText && streamActions.length === 0 && (
              <div className="flex items-center gap-1.5 rounded-xl bg-muted/65 px-3 py-2 text-xs text-muted-foreground">
                <LoaderIcon className="size-3 animate-spin" />
                <span>正在思考…</span>
              </div>
            )}
          </div>
        )}

        {streamError && (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{streamError}</div>
        )}
      </div>

      {/* 底部输入区 */}
      <div className="shrink-0 border-t bg-background p-3">
        {currentPaper && (
          <div className="mb-2 flex items-center gap-1.5 rounded-md bg-muted/50 px-2.5 py-1 text-[11px] text-muted-foreground">
            <span className="shrink-0">已附带当前文献：</span>
            <span className="min-w-0 truncate font-medium text-foreground" title={currentPaper.title}>
              {currentPaper.title}
            </span>
            {currentPaper.item && (
              <span className="shrink-0 text-[10px]">（{rating(currentPaper.item)}）</span>
            )}
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void sendMessage()
              }
            }}
            placeholder="向内置 Agent 提问或下达指令（Enter 发送）…"
            className="min-h-[56px] max-h-32 flex-1 resize-none rounded-lg border bg-background px-3 py-2 text-[13px] leading-snug outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          {streaming ? (
            <Button
              size="sm"
              variant="outline"
              className="h-9 shrink-0 px-2.5"
              onClick={handleStop}
              title="停止生成"
              aria-label="停止生成"
            >
              <SquareIcon className="size-3.5" />
            </Button>
          ) : (
            <Button
              size="sm"
              className="h-9 shrink-0 px-3"
              disabled={!input.trim()}
              onClick={() => void sendMessage()}
              title="发送（Enter）"
              aria-label="发送"
            >
              <SendIcon className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
    </aside>
  )
}
