import * as React from "react"
import { LanguagesIcon, LoaderIcon, RotateCcwIcon } from "lucide-react"

import { Card } from "@/components/page"
import { RelevanceBadge } from "@/components/relevance-badge"
import { Button } from "@/components/ui/button"
import { RELEVANCE, errorMessage, rating, type BatchPaper, type Item, type Relevance, type TranslationInfo } from "@/lib/api"
import { cn } from "@/lib/utils"

/** A paper as shown: what the batch or record says about it, plus its rating if it has one. */
export type Paper = (BatchPaper | Item) & { item: Item | null }

/**
 * The paper on a gray canvas at a readable width: details and abstract (the
 * agent's Chinese translation when it made one), the agent's rating, then the
 * user's controls, which stay in view at the bottom while the text scrolls.
 */
export function DetailPane({
  paper,
  edited,
  onRate,
  onSelect,
  onExclude,
  translation,
  onTranslate,
}: {
  paper: Paper | null
  edited: number
  onRate: (rel: Relevance) => void
  onSelect: () => void
  onExclude: () => void
  translation: TranslationInfo | null
  onTranslate: () => Promise<void>
}) {
  // Kept across papers: whoever prefers the original keeps seeing it.
  const [original, setOriginal] = React.useState(false)
  const [translating, setTranslating] = React.useState<{ key: string; error?: string } | null>(null)
  const tried = React.useRef(new Set<string>())  // auto-translate a paper once, even if that failed
  const key = paper ? paper.doi || paper.title : ""
  const item = paper?.item ?? null
  const canTranslate = Boolean(paper?.abstract && item && !item.abstract_zh && translation?.provider !== "agent")
  const run = React.useCallback(() => {
    setTranslating({ key })
    onTranslate().then(() => setTranslating(null)).catch((e) => setTranslating({ key, error: errorMessage(e) }))
  }, [key, onTranslate])
  React.useEffect(() => {
    if (!canTranslate || !translation?.ready || !item || tried.current.has(key)) return
    if (!translation.auto.includes(rating(item))) return
    tried.current.add(key)
    run()
  }, [canTranslate, translation, item, key, run])
  if (!paper) {
    return <div className="flex flex-1 items-center justify-center bg-canvas text-sm text-muted-foreground">选择文献以查看详情</div>
  }
  const rel = item ? rating(item) : null
  const busy = translating?.key === key && !translating.error
  const failed = translating?.key === key ? translating.error : undefined
  const zh = item?.abstract_zh
  const abstract = zh && !original ? zh : paper.abstract
  return (
    <div className="scrollable min-h-0 flex-1 overflow-y-auto bg-canvas">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-4 px-8 py-6">
        <Card className="space-y-4 p-6">
          <div className="flex flex-wrap items-center gap-2">
            <RelevanceBadge rel={rel} />
            {paper.journal_rank && <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{paper.journal_rank}</span>}
            {paper.cited_by_count != null && <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">被引 {paper.cited_by_count}</span>}
            {paper.retracted && <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-xs text-destructive">已撤稿</span>}
          </div>
          <h1 className="text-xl leading-snug font-semibold">{paper.title}</h1>
          <p className="text-sm text-muted-foreground">
            {[paper.authors?.join(", "), paper.year, paper.journal].filter(Boolean).join(" · ")}
            {paper.doi && <> · <span className="font-mono text-xs">{paper.doi}</span></>}
          </p>
          <section className="space-y-2 border-t pt-4">
            <div className="flex items-center justify-between gap-4">
              <h2 className="text-sm font-semibold">摘要</h2>
              {zh && (
                <div role="group" aria-label="摘要语言" className="inline-flex rounded-md bg-muted p-0.5 text-xs">
                  {[[false, "译文"], [true, "原文"]].map(([o, label]) => (
                    <button key={String(o)} type="button" aria-pressed={original === o} onClick={() => setOriginal(o as boolean)}
                      className={cn("rounded px-2.5 py-1 text-muted-foreground",
                        original === o && "bg-background font-medium text-foreground shadow-xs")}>
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <p className="text-[15px] leading-7">{abstract || "暂无摘要，仅依据标题评分。"}</p>
            {busy && <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><LoaderIcon className="size-3 animate-spin" />正在翻译…</p>}
            {failed && <p className="text-xs text-destructive">{failed}</p>}
            {canTranslate && !busy && (
              translation?.ready ? (
                <Button size="sm" variant="outline" onClick={run}><LanguagesIcon />{failed ? "重新翻译" : "翻译摘要"}</Button>
              ) : (
                <p className="text-xs text-muted-foreground">在「设置 → 筛选」中填写翻译服务的 Key 后可翻译摘要</p>
              )
            )}
          </section>
        </Card>

        {item && (
          <Card className="space-y-1.5 px-6 py-4">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">Agent 的评分 <RelevanceBadge rel={item.relevance} /></div>
            <p className="text-sm leading-relaxed">{item.reason}</p>
          </Card>
        )}

        <div className="flex-1" />
        <Card className="sticky bottom-4 space-y-3 px-5 py-4 shadow-sm">
          {item ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="text-xs text-muted-foreground">我的评分</span>
              <div role="group" aria-label="我的评分" className="inline-flex rounded-lg bg-muted p-0.5">
                {RELEVANCE.map((r, i) => (
                  <button key={r} type="button" aria-pressed={rel === r} onClick={() => onRate(r)}
                    className={cn("flex h-8 items-center gap-1.5 rounded-md px-3 text-sm text-muted-foreground hover:text-foreground",
                      rel === r && "bg-background font-medium text-foreground shadow-xs")}>
                    {r}<span className="text-[10px] text-muted-foreground/70 tabular-nums">{i + 1}</span>
                  </button>
                ))}
              </div>
              {item.user_relevance && (
                <button type="button" onClick={() => onRate(item.relevance)}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                  <RotateCcwIcon className="size-3" />恢复 Agent 的评分
                </button>
              )}
              <div className="ml-auto flex gap-2">
                <Button variant="outline" onClick={onExclude}>{item.decision === "rejected" ? "取消排除" : "排除"}</Button>
                <Button className="min-w-28" variant={item.decision === "selected" ? "outline" : "default"} onClick={onSelect}>
                  {item.decision === "selected" ? "移出下载" : "加入下载"}
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Agent 尚未评分，评分后可在此修改。</p>
          )}
          {edited > 0 && (
            <p className="rounded-lg border border-warning-foreground/20 bg-warning px-3 py-2 text-xs text-warning-foreground">
              你改了 {edited} 篇的评分。回到对话说「继续」，下一批会按你的评分排序。
            </p>
          )}
          <p className="text-xs text-muted-foreground">快捷键：↑↓ 切换 · 1–4 评分 · 空格 加入下载</p>
        </Card>
      </div>
    </div>
  )
}
