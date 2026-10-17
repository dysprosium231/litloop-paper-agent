import * as React from "react"
import { CheckCircle2Icon, InboxIcon, LoaderIcon, XCircleIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { SidebarContent, SidebarHeader } from "@/components/ui/sidebar"
import { downloadsApi, zoteroApi, type ZoteroHistoryEntry, type ZoteroOverview, type ZoteroPendingGroup } from "@/lib/api"
import { cn } from "@/lib/utils"

/** Connection, what waits for Zotero and what was pushed; polls faster while a push runs. */
export function useZotero(active: boolean) {
  const [data, setData] = React.useState<ZoteroOverview | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const refresh = React.useCallback(() => {
    zoteroApi.overview().then((d) => { setData(d); setError(null) }).catch((e) => setError(String(e)))
  }, [])
  const pushing = Boolean(data?.pushing)
  React.useEffect(() => {
    if (!active) return
    refresh()
    const t = setInterval(refresh, pushing ? 1500 : 5000)
    return () => clearInterval(t)
  }, [active, pushing, refresh])
  return { data, error, refresh }
}

const outcome = (h: ZoteroHistoryEntry) =>
  [h.added && `新增 ${h.added}`, h.reused && `已存在 ${h.reused}`, h.no_pdf && `附件缺失 ${h.no_pdf}`, h.failed && `失败 ${h.failed}`]
    .filter(Boolean).join(" · ") || "无推送项"

const errorText = (e: unknown) => String(e).replace(/^Error: \d+ /, "").replace(/^\{"detail":"(.*)"\}$/, "$1")

function Check({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <div className="flex items-center gap-1.5">
      {ok ? <CheckCircle2Icon className="size-3.5 text-emerald-600 dark:text-emerald-400" /> : <XCircleIcon className="size-3.5 text-destructive" />}
      {ok ? yes : no}
    </div>
  )
}

/** Second column of the Zotero section: connection, the pending queue, past pushes (they open the download job). */
export function ZoteroNav({ data, onOpenJob }: { data: ZoteroOverview | null; onOpenJob: (job: string) => void }) {
  const waiting = data?.pending.reduce((n, g) => n + g.papers.length, 0) ?? 0
  return (
    <>
      <SidebarHeader className="border-b p-4">
        <div className="text-base font-medium text-foreground">Zotero</div>
        {data && (
          <div className="space-y-1 pt-1 text-xs text-muted-foreground">
            <Check ok={data.running} yes="Zotero 运行中" no="Zotero 未运行" />
            <Check ok={Boolean(data.mcp)} yes="zotero-mcp 已就绪" no="未找到 zotero-mcp（设置 → Zotero）" />
          </div>
        )}
      </SidebarHeader>
      <SidebarContent className="scrollable gap-0.5 p-2">
        <div className="flex w-full items-center gap-3 rounded-lg bg-sidebar-accent px-3 py-2 text-sm font-medium">
          <InboxIcon className="size-4 shrink-0" />
          <span className="flex-1">推送队列</span>
          {waiting > 0 && <span className="rounded-full bg-primary px-1.5 text-xs font-medium text-primary-foreground">{waiting}</span>}
        </div>
        {!!data?.history.length && <div className="px-3 pt-4 pb-1 text-xs text-muted-foreground">历史记录</div>}
        {data?.history.map((h) => (
          <button key={h.job} type="button" onClick={() => onOpenJob(h.job)}
            className="flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left hover:bg-sidebar-accent">
            <span className="line-clamp-2 text-sm">{h.title}</span>
            <span className="truncate text-xs text-muted-foreground" title={h.collection}>{h.collection}</span>
            <span className={cn("flex items-center gap-1.5 text-xs text-muted-foreground", (h.failed > 0 || h.no_pdf > 0) && "text-warning-foreground")}>
              {h.state === "running" && <LoaderIcon className="size-3 animate-spin" />}
              {h.state === "running" ? "进行中" : h.state === "interrupted" ? "已中断" : outcome(h)}
            </span>
          </button>
        ))}
      </SidebarContent>
    </>
  )
}

/** Downloaded papers not in Zotero yet, by download job; push a group to its collection. */
export function ZoteroPane({ data, error, onChanged, onOpenJob }: {
  data: ZoteroOverview | null; error: string | null; onChanged: () => void; onOpenJob: (job: string) => void
}) {
  const [failure, setFailure] = React.useState<string | null>(null)
  if (!data) return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">{error ?? "加载中…"}</div>
  const push = (g: ZoteroPendingGroup) => {
    setFailure(null)
    downloadsApi.push(g.job, g.collection).then(onChanged).catch((e) => setFailure(errorText(e)))
  }
  const all = data.pending.reduce((n, g) => n + g.papers.length, 0)
  const blocked = !data.mcp || !data.running || Boolean(data.pushing)
  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollable">
      <div className="border-b px-6 py-4">
        <h2 className="text-lg font-semibold">推送队列</h2>
        <div className="mt-0.5 text-xs text-muted-foreground">{all ? `${all} 篇 · ${data.pending.length} 个下载任务` : "已全部推送"}</div>
      </div>
      {failure && <p className="mx-6 mt-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">{failure}</p>}
      {!data.running && all > 0 && <p className="mx-6 mt-4 rounded-md bg-warning px-3 py-2 text-sm text-warning-foreground">Zotero 未运行，请先启动 Zotero。</p>}
      {data.pushing && (
        <p className="mx-6 mt-4 flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderIcon className="size-3.5 animate-spin" />当前有推送任务进行中。
        </p>
      )}
      {all === 0 && (
        <div className="px-6 py-12 text-center">
          <p className="text-sm font-medium">暂无待推送文献</p>
          <p className="mt-1 text-xs text-muted-foreground">下载完成的文献将显示在此处</p>
        </div>
      )}
      {data.pending.map((g) => (
        <section key={g.job} className="border-b px-6 py-4">
          <div className="mb-2 flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="truncate text-sm font-medium">{g.title}</h3>
              <div className="truncate text-xs text-muted-foreground">目标分类：{g.collection}</div>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button size="sm" variant="ghost" onClick={() => onOpenJob(g.job)}>更改分类</Button>
              <Button size="sm" disabled={blocked} onClick={() => push(g)}>推送</Button>
            </div>
          </div>
          <ul className="space-y-1.5">
            {g.papers.map((p) => (
              <li key={p.doi} className="text-sm">
                <div className="line-clamp-2">{p.title || p.doi}</div>
                <div className="text-xs text-muted-foreground">
                  {[p.year, p.journal].filter(Boolean).join(" · ") || p.doi}
                  {p.zotero === "no_pdf" ? " · 上次附件缺失" : p.zotero === "failed" ? " · 上次推送失败" : ""}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
