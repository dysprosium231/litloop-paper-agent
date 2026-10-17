import { RotateCcwIcon } from "lucide-react"

import { RelevanceBadge } from "@/components/relevance-badge"
import { Button } from "@/components/ui/button"
import { RELEVANCE, rating, type BatchPaper, type Item, type Relevance } from "@/lib/api"
import { cn } from "@/lib/utils"

/** A paper as shown: what the batch or record says about it, plus its rating if it has one. */
export type Paper = (BatchPaper | Item) & { item: Item | null }

export function DetailPane({
  paper,
  edited,
  onRate,
  onSelect,
  onExclude,
}: {
  paper: Paper | null
  edited: number
  onRate: (rel: Relevance) => void
  onSelect: () => void
  onExclude: () => void
}) {
  if (!paper) {
    return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">选择文献以查看详情</div>
  }
  const item = paper.item
  const rel = item ? rating(item) : null
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 space-y-4 overflow-auto px-6 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <RelevanceBadge rel={rel} />
          {paper.journal_rank && <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{paper.journal_rank}</span>}
          {paper.cited_by_count != null && <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">被引 {paper.cited_by_count}</span>}
          {paper.retracted && <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-xs text-destructive">已撤稿</span>}
        </div>
        <h1 className="text-lg leading-snug font-semibold">{paper.title}</h1>
        <p className="text-sm text-muted-foreground">
          {[paper.authors?.join(", "), paper.year, paper.journal].filter(Boolean).join(" · ")}
          {paper.doi && <> · <span className="font-mono text-xs">{paper.doi}</span></>}
        </p>
        <section className="space-y-1.5">
          <h2 className="text-xs text-muted-foreground">摘要</h2>
          <p className="text-sm leading-relaxed">{paper.abstract || "暂无摘要，仅依据标题评分。"}</p>
        </section>
        {item && (
          <section className="space-y-1 rounded-lg border bg-muted/40 p-3">
            <h2 className="text-xs text-muted-foreground">Agent 的判断：{item.relevance}</h2>
            <p className="text-sm">{item.reason}</p>
          </section>
        )}
      </div>

      <div className="space-y-3 border-t px-6 py-4">
        {item ? (
          <>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">我的评分</span>
              {item.user_relevance && (
                <button type="button" onClick={() => onRate(item.relevance)}
                  className="ml-auto flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                  <RotateCcwIcon className="size-3" />恢复 Agent 的评分
                </button>
              )}
            </div>
            <div role="group" aria-label="我的评分" className="grid grid-cols-4 gap-2">
              {RELEVANCE.map((r, i) => (
                <Button key={r} variant="outline" aria-pressed={rel === r} onClick={() => onRate(r)}
                  className={cn("h-11 flex-col gap-0", rel === r && "border-primary bg-primary/10 font-semibold dark:border-primary dark:bg-primary/20")}>
                  <span className="text-[11px] text-muted-foreground">{i + 1}</span>
                  <span>{r}</span>
                </Button>
              ))}
            </div>
            <div className="flex gap-2">
              <Button className="flex-1" onClick={onSelect}>
                {item.decision === "selected" ? "移出下载" : "加入下载"}
              </Button>
              <Button variant="outline" onClick={onExclude}>{item.decision === "rejected" ? "取消排除" : "排除"}</Button>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Agent 尚未评分，评分后可在此修改。</p>
        )}
        {edited > 0 && (
          <p className="rounded-lg border border-warning-foreground/20 bg-warning px-3 py-2 text-xs text-warning-foreground">
            你改了 {edited} 篇的评分。回到对话说“继续”，下一批会按你的评分排序。
          </p>
        )}
        <p className="text-xs text-muted-foreground">快捷键：↑↓ 切换 · 1–4 评分 · 空格 加入下载</p>
      </div>
    </div>
  )
}
