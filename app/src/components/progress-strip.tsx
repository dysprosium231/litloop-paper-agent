import type { Progress, Summary } from "@/lib/api"
import { cn } from "@/lib/utils"

const PHASES: [string, string][] = [
  ["keywords", "关键词"],
  ["expand", "扩展候选池"],
  ["abstracts", "补全摘要"],
  ["rank", "排序"],
  ["batch", "生成批次"],
]

const secs = (s: number) => (s >= 60 ? `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒` : `${Math.round(s)} 秒`)

/** What the running round is doing, or a one-line overview when nothing runs. */
export function ProgressStrip({ progress, summary }: { progress: Progress | null; summary: Summary | null }) {
  const running = progress?.state === "running"
  if (!running) {
    if (!summary) return null
    const highs = summary.batches.map((b) => (b.rated ? b.高 / b.rated : 0))
    return (
      <div className="flex items-center gap-4 border-b px-6 py-2.5 text-xs text-muted-foreground">
        <span>候选池 {summary.pool.toLocaleString()} 篇 · 已评 {summary.screened} 篇</span>
        <span className="flex h-4 items-end gap-0.5" role="img" aria-label="每批高相关比例">
          {highs.map((h, i) => (
            <span key={i} className="w-1.5 rounded-sm bg-primary" style={{ height: `${Math.max(8, h * 100)}%`, opacity: i === highs.length - 1 ? 1 : 0.45 }} />
          ))}
        </span>
        {summary.stop_suggested && <span className="rounded bg-warning px-2 py-0.5 text-warning-foreground">建议停止：{summary.stop_reason}</span>}
        {summary.api_warnings?.map((w) => <span key={w} className="truncate">{w}</span>)}
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
                  {done.has(id) ? secs(done.get(id)!) : isNow && now?.total ? `${now.done} / ${now.total}` : ""}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
