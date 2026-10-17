import * as React from "react"
import { CheckCircle2Icon, LoaderIcon, XCircleIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ZoteroCheckPane } from "@/components/zotero-check"
import { downloadsApi, zoteroApi, type WriteMode, type ZoteroHistoryEntry, type ZoteroOverview, type ZoteroPendingGroup, errorMessage } from "@/lib/api"
import { cn } from "@/lib/utils"

/** Connection, what waits for Zotero and what was pushed; polls faster while a push runs. */
export function useZotero(active: boolean) {
  const [data, setData] = React.useState<ZoteroOverview | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const refresh = React.useCallback(() => {
    zoteroApi.overview().then((d) => { setData(d); setError(null) }).catch((e) => setError(errorMessage(e)))
  }, [])
  const pushing = Boolean(data?.pushing)
  React.useEffect(() => {
    if (!active) return
    refresh()
    const t = setInterval(refresh, pushing ? 1500 : 5000)
    return () => clearInterval(t)
  }, [active, pushing, refresh])
  // The write mode costs a zotero-mcp start; ask once per visit and after authorizing.
  const [mode, setMode] = React.useState<WriteMode | null | undefined>(undefined)
  const loadMode = React.useCallback(() => {
    zoteroApi.writeMode().then((r) => setMode(r.mode)).catch(() => setMode(null))
  }, [])
  React.useEffect(() => {
    if (active && data?.mcp) loadMode()
  }, [active, data?.mcp, loadMode])
  return { data, error, refresh, mode, loadMode }
}

const MODE: Record<WriteMode, string> = {
  local: "写入方式：本地",
  hybrid: "写入方式：网页接口（不推荐）",
  web: "写入方式：网页接口（不推荐）",
  none: "未授权写入",
}

/** Write mode line; offers local-write authorization (Zotero asks the user in its own dialog). */
function WriteModeLine({ mode, running, onDone }: { mode: WriteMode | null | undefined; running: boolean; onDone: () => void }) {
  const [asking, setAsking] = React.useState(false)
  const [note, setNote] = React.useState<string | null>(null)
  if (mode === undefined) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <LoaderIcon className="size-4 animate-spin" />写入方式：检测中…
      </div>
    )
  }
  const authorize = () => {
    setAsking(true)
    setNote("请在 Zotero 弹出的对话框中选择「始终允许」")
    zoteroApi.authorize().then((r) => setNote(r.ok ? null : r.message ?? "未获得授权，可重新授权")).catch((e) => setNote(errorMessage(e)))
      .finally(() => { setAsking(false); onDone() })
  }
  return (
    <div className="space-y-1">
      <Check ok={mode === "local"} yes={MODE.local} no={mode ? MODE[mode] : "写入方式未知"} />
      {mode !== "local" && running && (
        <Button size="sm" variant="outline" className="h-7 text-xs" disabled={asking} onClick={authorize}>
          {asking && <LoaderIcon className="animate-spin" />}授权本地写入
        </Button>
      )}
      {note && <p className="text-xs text-warning-foreground">{note}</p>}
    </div>
  )
}

const outcome = (h: ZoteroHistoryEntry) =>
  [h.added && `新增 ${h.added}`, h.reused && `已存在 ${h.reused}`, h.no_pdf && `附件缺失 ${h.no_pdf}`, h.failed && `失败 ${h.failed}`]
    .filter(Boolean).join(" · ") || "无推送项"


function Check({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <div className="flex items-center gap-1.5">
      {ok ? <CheckCircle2Icon className="size-3.5 text-emerald-600 dark:text-emerald-400" /> : <XCircleIcon className="size-3.5 text-destructive" />}
      {ok ? yes : no}
    </div>
  )
}

export type ZoteroView = "queue" | "check" | "history"

/**
 * The Zotero page: connection and write mode on top, then tabs for the push
 * queue, the attachment check and past pushes (they open the download job).
 */
export function ZoteroPage({ data, error, mode, onModeChanged, onChanged, onOpenJob, view, onView }: {
  data: ZoteroOverview | null; error: string | null; mode: WriteMode | null | undefined; onModeChanged: () => void
  onChanged: () => void; onOpenJob: (job: string) => void; view: ZoteroView; onView: (v: ZoteroView) => void
}) {
  const waiting = data?.pending.reduce((n, g) => n + g.papers.length, 0) ?? 0
  const tabs: { id: ZoteroView; label: string; count?: number }[] = [
    { id: "queue", label: "推送队列", count: waiting },
    { id: "check", label: "附件检查" },
    { id: "history", label: "推送记录", count: data?.history.length },
  ]
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-stretch gap-5 border-b px-6">
        <h2 className="flex items-center py-3 text-lg font-semibold">Zotero</h2>
        <div className="my-4 w-px bg-border" />
        <div role="tablist" className="flex gap-6">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={view === t.id} onClick={() => onView(t.id)}
              className={cn("-mb-px flex items-center gap-1.5 border-b-2 text-sm text-muted-foreground hover:text-foreground",
                view === t.id ? "border-primary font-medium text-foreground" : "border-transparent")}>
              {t.label}
              {!!t.count && <span className="rounded-full bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">{t.count}</span>}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b px-6 py-2.5 text-xs text-muted-foreground">
        {data ? (
          <>
            <Check ok={data.running} yes="Zotero 运行中" no="Zotero 未运行" />
            <Check ok={Boolean(data.mcp)} yes="zotero-mcp 已就绪" no="未找到 zotero-mcp（「设置 → Zotero」）" />
            {data.mcp && <WriteModeLine mode={mode} running={data.running} onDone={onModeChanged} />}
          </>
        ) : (error ?? "正在加载…")}
      </div>
      {view === "check" ? (
        <ZoteroCheckPane running={Boolean(data?.running)} onStarted={onOpenJob} />
      ) : view === "history" ? (
        <ZoteroHistory history={data?.history ?? []} onOpenJob={onOpenJob} />
      ) : (
        <ZoteroPane data={data} error={error} onChanged={onChanged} onOpenJob={onOpenJob} />
      )}
    </div>
  )
}

function ZoteroHistory({ history, onOpenJob }: { history: ZoteroHistoryEntry[]; onOpenJob: (job: string) => void }) {
  if (!history.length) {
    return (
      <div className="px-6 py-12 text-center">
        <p className="text-sm font-medium">暂无推送记录</p>
        <p className="mt-1 text-xs text-muted-foreground">推送到 Zotero 的下载任务将显示在此处</p>
      </div>
    )
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollable">
      <ul className="mx-6 my-4 divide-y rounded-lg border">
        {history.map((h) => (
          <li key={h.job}>
            <button type="button" onClick={() => onOpenJob(h.job)}
              className="flex w-full items-center gap-4 px-4 py-2.5 text-left hover:bg-accent/60">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{h.title}</div>
                <div className="truncate text-xs text-muted-foreground" title={h.collection}>{h.collection}</div>
              </div>
              <span className={cn("flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground",
                (h.failed > 0 || h.no_pdf > 0) && "text-warning-foreground")}>
                {h.state === "running" && <LoaderIcon className="size-3 animate-spin" />}
                {h.state === "running" ? "进行中" : h.state === "interrupted" ? "已中断" : outcome(h)}
              </span>
              <span className="w-24 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{(h.finished ?? h.started ?? "").slice(5, 16)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Downloaded papers not in Zotero yet, by download job; push a group to its collection. */
export function ZoteroPane({ data, error, onChanged, onOpenJob }: {
  data: ZoteroOverview | null; error: string | null; onChanged: () => void; onOpenJob: (job: string) => void
}) {
  const [failure, setFailure] = React.useState<string | null>(null)
  if (!data) return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">{error ?? "正在加载…"}</div>
  const push = (g: ZoteroPendingGroup) => {
    setFailure(null)
    downloadsApi.push(g.job, g.collection).then(onChanged).catch((e) => setFailure(errorMessage(e)))
  }
  const all = data.pending.reduce((n, g) => n + g.papers.length, 0)
  const blocked = !data.mcp || !data.running || Boolean(data.pushing)
  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollable">
      {all > 0 && <div className="px-6 pt-4 text-xs text-muted-foreground">{all} 篇 · {data.pending.length} 个下载任务</div>}
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
