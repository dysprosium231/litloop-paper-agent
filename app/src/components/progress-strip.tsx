import * as React from "react"
import { TriangleAlertIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { ErrorAction, Progress, SessionOrigin, Summary } from "@/lib/api"
import { cn, duration } from "@/lib/utils"

const PHASES: [string, string][] = [
  ["keywords", "关键词"],
  ["expand", "扩展候选池"],
  ["abstracts", "补全摘要"],
  ["rank", "排序"],
  ["batch", "生成批次"],
]
const PHASE_LABEL: Record<string, string> = Object.fromEntries(PHASES)

const secs = duration

/**
 * Share of 高 in each batch, newest on the right, scaled to the best batch. Each bar answers hover over its
 * whole column (full height, gaps included), and the note shows at once.
 */
function BatchBars({ batches, highs }: { batches: Summary["batches"]; highs: number[] }) {
  const [hover, setHover] = React.useState<number | null>(null)
  const b = hover === null ? null : batches[hover]
  const top = Math.max(...highs)  // the best batch fills the height; the % next to it stays the real share
  return (
    <span className="relative flex shrink-0 items-center gap-2" onMouseLeave={() => setHover(null)}>
      <span className="flex h-7 items-stretch" role="img" aria-label="每批高相关比例">
        {batches.map((_, i) => (
          <span key={i} className="flex cursor-default items-end px-0.5" onMouseEnter={() => setHover(i)}>
            <span className={cn("w-2.5 rounded-sm bg-primary", hover === i && "ring-2 ring-primary/30")}
              style={{ height: `${top > 0 ? Math.max(8, (highs[i] / top) * 100) : 8}%`, opacity: hover === i || i === highs.length - 1 ? 1 : 0.45 }} />
          </span>
        ))}
      </span>
      <span className="font-medium text-foreground tabular-nums" onMouseEnter={() => setHover(null)}>
        {Math.round(highs[highs.length - 1] * 100)}%
      </span>
      {b && (
        <span className="pointer-events-none absolute top-full left-0 z-50 mt-2 w-max rounded-md border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow-md">
          第 {b.batch} 批：高 {b.高} / 已评分 {b.rated}（{Math.round(highs[hover!] * 100)}%）
        </span>
      )}
    </span>
  )
}

/** A button that opens a small panel under it; outside click or Esc closes it. */
function Pop({ label, aria, className, panelClassName, children }: {
  label: React.ReactNode; aria?: string; className?: string; panelClassName?: string; children: React.ReactNode
}) {
  const [open, setOpen] = React.useState(false)
  const root = React.useRef<HTMLSpanElement>(null)
  React.useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", close)
    document.addEventListener("keydown", esc)
    return () => {
      document.removeEventListener("mousedown", close)
      document.removeEventListener("keydown", esc)
    }
  }, [open])
  return (
    <span ref={root} className="relative">
      <button type="button" aria-label={aria} aria-expanded={open} onClick={() => setOpen((o) => !o)} className={className}>{label}</button>
      {open && (
        <div className={cn("absolute top-full right-0 z-50 mt-2 w-80 rounded-lg border bg-popover p-3 text-sm text-popover-foreground shadow-md", panelClassName)}>
          {children}
        </div>
      )}
    </span>
  )
}

function ChatDetailPop({ origin }: { origin: SessionOrigin }) {
  const [copied, setCopied] = React.useState(false)
  const run = origin.last_run
  if (!run) return null
  const sessionId = run.agent_session || origin.agent_session
  if (!run.prompt && !run.result && !sessionId && !run.usage) return null
  const agentLabel = run.harness_label || origin.harness_label || "Agent"

  const copyPrompt = () => {
    if (!run.prompt) return
    navigator.clipboard.writeText(run.prompt).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }

  return (
    <Pop
      label="对话详情"
      panelClassName="w-96 max-w-[90vw] space-y-2.5"
      className="rounded border px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      <div className="space-y-1 text-xs">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">执行 Agent</span>
          <span className="font-medium text-foreground">
            {agentLabel}{run.resumed_chat ? " · 原对话续接" : ""}
          </span>
        </div>
        {sessionId && (
          <div className="flex items-center justify-between gap-2">
            <span className="shrink-0 text-muted-foreground">会话 ID</span>
            <code className="truncate font-mono text-[11px] text-foreground" title={sessionId}>{sessionId}</code>
          </div>
        )}
        {run.usage && (
          <div className="flex items-center justify-between gap-2">
            <span className="shrink-0 text-muted-foreground">Token 消耗</span>
            <span className="text-right tabular-nums text-foreground">
              总计 {run.usage.total_tokens.toLocaleString()} · 输入 {run.usage.input_tokens.toLocaleString()} / 输出 {run.usage.output_tokens.toLocaleString()}
              {run.usage.cost_text ? `（${run.usage.cost_text}）` : ""}
            </span>
          </div>
        )}
      </div>
      {run.prompt && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">发送给 Agent 的提示词</span>
            <button
              type="button"
              onClick={copyPrompt}
              className="rounded px-1.5 py-0.5 text-xs text-primary hover:bg-primary/10"
            >
              {copied ? "已复制" : "复制提示词"}
            </button>
          </div>
          <pre className="max-h-40 overflow-auto rounded-md border bg-muted/50 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-foreground">
            {run.prompt}
          </pre>
        </div>
      )}
      {run.result && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-muted-foreground">Agent 最终回复</div>
          <div className="max-h-36 overflow-auto rounded-md border bg-muted/30 p-2 text-xs leading-relaxed whitespace-pre-line text-foreground">
            {run.result}
          </div>
        </div>
      )}
    </Pop>
  )
}

/** What the running round is doing, or a one-line overview when nothing runs. */
export function ProgressStrip({ progress, summary, resumable, origin, onContinue, onResume, onErrorAction }: {
  progress: Progress | null
  summary: Summary | null
  resumable?: boolean
  origin?: SessionOrigin | null
  onContinue: () => void
  onResume?: (forceNew?: boolean) => void
  onErrorAction?: (action: ErrorAction) => void
}) {
  const [copiedCmd, setCopiedCmd] = React.useState(false)
  const running = progress?.state === "running"
  if (!running) {
    const failed = progress?.state === "failed"
    const interrupted = progress?.state === "interrupted"
    if (!summary && !resumable && !failed && !interrupted) return null
    const highs = summary ? summary.batches.map((b) => (b.rated ? b.高 / b.rated : 0)) : []
    const phaseLabel = progress?.stopped_phase ? (PHASE_LABEL[progress.stopped_phase] ?? progress.stopped_phase) : null
    const canAct = Boolean(summary?.current_batch || resumable)
    const isExternalWithPrompt = origin?.source === "external" && Boolean(origin.continue_prompt)
    const copyContinueCmd = () => {
      if (!origin?.continue_prompt) return
      navigator.clipboard.writeText(origin.continue_prompt).then(() => {
        setCopiedCmd(true)
        setTimeout(() => setCopiedCmd(false), 1800)
      }).catch(() => {})
    }
    return (
      <div className="flex min-h-13 min-w-0 flex-wrap items-center gap-x-5 gap-y-2 border-b px-6 py-3 text-sm text-muted-foreground">
        {summary && (
          <span className="shrink-0 whitespace-nowrap tabular-nums" title={`候选池 ${summary.pool.toLocaleString()} 篇，已评分 ${summary.screened.toLocaleString()} 篇`}>
            已评分 <span className="font-medium text-foreground">{summary.screened.toLocaleString()}</span> / {summary.pool.toLocaleString()}
          </span>
        )}
        {highs.length > 0 && <BatchBars batches={summary!.batches} highs={highs} />}
        {interrupted && (
          <span className="rounded bg-warning px-2 py-0.5 text-xs text-warning-foreground">
            {phaseLabel ? `上次检索在「${phaseLabel}」中断` : "上次检索已中断"}
          </span>
        )}
        {failed && (
          <span className="flex items-center gap-2">
            <span className="max-w-md truncate rounded bg-destructive/10 px-2 py-0.5 text-xs text-destructive"
              title={progress?.error ?? undefined}>
              {progress?.error_text || "无法继续检索：上一轮未完成"}
            </span>
            {progress?.error_action && progress.error_action.kind !== "retry" && onErrorAction && (
              <Button size="sm" variant="outline" className="h-6 px-2 text-xs"
                onClick={() => onErrorAction(progress.error_action!)}>
                {progress.error_action.label}
              </Button>
            )}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {summary?.stop_suggested && (
            <Pop label="建议停止" className="rounded bg-warning px-2 py-0.5 text-warning-foreground hover:brightness-95">
              <p className="font-medium">建议停止</p>
              <p className="mt-1 text-muted-foreground">{summary.stop_reason}</p>
            </Pop>
          )}
          {!!summary?.api_warnings?.length && (
            <Pop label={<><TriangleAlertIcon className="size-4" /><span className="tabular-nums">{summary.api_warnings.length}</span></>}
              aria={`接口提醒 ${summary.api_warnings.length} 条`}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-warning-foreground hover:bg-warning">
              <p className="font-medium">接口提醒</p>
              <ul className="mt-1 space-y-1 text-muted-foreground">
                {summary.api_warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </Pop>
          )}
          {origin?.last_run && <ChatDetailPop origin={origin} />}
          {canAct && isExternalWithPrompt ? (
            <>
              <Button
                size="sm"
                onClick={copyContinueCmd}
                title="此会话在外部 Agent 窗口发起；复制指令后粘贴回原对话窗口，即可不丢上下文继续下一批"
              >
                {copiedCmd ? "已复制指令" : "复制『继续』指令（回原对话粘贴）"}
              </Button>
              {onResume && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onResume(true)}
                  title="在 app 后台启动 Agent 接手继续深度检索"
                >
                  在 app 后台接手继续
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={onContinue}
                title="按当前全部评分重新排序并生成下一批，不经过 Agent"
              >
                仅取下一批
              </Button>
            </>
          ) : (
            <>
              {canAct && onResume && (
                origin?.can_resume_same_chat ? (
                  <Button
                    size="sm"
                    onClick={() => onResume(false)}
                    title={`回到上次的 ${origin.harness_label || "Agent"} 对话（${origin.agent_session}）接着筛选下一批`}
                  >
                    由 Agent 继续（原对话）
                  </Button>
                ) : origin?.harness === "builtin" ? (
                  <Button
                    size="sm"
                    onClick={() => onResume(false)}
                    title="让内置 Agent 接着评分并继续深度检索"
                  >
                    由内置 Agent 继续
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    onClick={() => onResume(false)}
                    title="让 Agent 接着评分并继续深度检索"
                  >
                    由 Agent 继续
                  </Button>
                )
              )}
              {canAct && (
                <Button
                  size="sm"
                  variant={onResume ? "outline" : "default"}
                  onClick={onContinue}
                  title="按当前全部评分重新排序并生成下一批，不经过 Agent"
                >
                  仅取下一批
                </Button>
              )}
            </>
          )}
        </span>
      </div>
    )
  }
  const done = new Map(progress.phases.map((p) => [p.name, p.seconds]))
  const now = progress.phase
  const elapsed = progress.updated - progress.started
  let eta = ""
  if (now?.total && now.done >= now.total) {
    eta = " · 即将完成"  // counted out, still writing results: "约还要 0 秒" read as stuck
  } else if (now?.total && now.done > 0) {
    const rate = (progress.updated - now.started) / now.done
    eta = ` · 预计剩余 ${secs(Math.max(1, rate * (now.total - now.done)))}`
  }
  return (
    <div className="space-y-2 border-b px-6 py-3">
      <div className="flex items-baseline gap-2 text-sm">
        <span className="font-medium">{progress.event === "start" ? "正在构建候选池" : "正在生成下一批"}</span>
        <span className="text-xs text-muted-foreground">用时 {secs(elapsed)}{eta}</span>
      </div>
      <div className="grid grid-cols-5 gap-1.5">
        {PHASES.map(([id, label]) => {
          const isNow = now?.name === id
          const pct = done.has(id) ? 100 : isNow && now?.total ? (now.done / now.total) * 100 : isNow ? 15 : 0
          return (
            <div key={id} className="space-y-1">
              <div className="h-1 overflow-hidden rounded-full bg-muted">
                <div className={cn("h-full rounded-full bg-primary transition-all", done.has(id) && "opacity-45")} style={{ width: `${pct}%` }} />
              </div>
              <div className="flex justify-between text-[11px]">
                <span className={cn(isNow ? "font-medium text-foreground" : "text-muted-foreground")}>{label}</span>
                <span className="text-muted-foreground">
                  {done.has(id) ? secs(done.get(id)!) : isNow && now?.total ? `${now.done}/${now.total}` : ""}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
