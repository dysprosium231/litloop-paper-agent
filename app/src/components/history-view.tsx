import * as React from "react"
import { ChevronDownIcon, ChevronRightIcon, FolderOpenIcon } from "lucide-react"

import { Card, Empty, PageBody, PageHeader } from "@/components/page"
import { Button } from "@/components/ui/button"
import { downloadsApi, errorMessage, historyApi, watchesApi, type HistoryEntry, type PrismaSummary, type SearchHit, type Watch } from "@/lib/api"
import { cn } from "@/lib/utils"

const MODE = { quick: "快速检索", deep: "深度检索" } as Record<string, string>

/**
 * Screening history: every session, grouped by its question, with how far it
 * got and the final picks; the picks can be exported or sent to the downloads.
 */
export function HistoryPage({ onBack, onOpen, onDownloaded }: {
  onBack: () => void; onOpen: (session: string) => void; onDownloaded: (job: string) => void
}) {
  const [entries, setEntries] = React.useState<HistoryEntry[] | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  React.useEffect(() => { historyApi.list().then(setEntries).catch((e) => setError(errorMessage(e))) }, [])
  const groups = React.useMemo(() => {
    const m = new Map<string, HistoryEntry[]>()
    for (const e of entries ?? []) m.set(e.question || e.session, [...(m.get(e.question || e.session) ?? []), e])
    return [...m.entries()]
  }, [entries])
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="筛选历史">
        <Button size="sm" variant="outline" onClick={onBack}>返回筛选</Button>
      </PageHeader>
      <PageBody wide>
        {error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        <WatchesCard />
        {entries && groups.length === 0 && <Card><Empty title="暂无检索记录" /></Card>}
        {!entries && !error && <p className="text-sm text-muted-foreground">正在读取…</p>}
        {groups.map(([question, sessions]) => (
          <Card key={question}>
            <div className="border-b px-5 py-3">
              <h2 className="line-clamp-2 text-[15px] font-semibold">{question}</h2>
              <div className="mt-0.5 text-xs text-muted-foreground">{sessions.length} 个检索会话</div>
            </div>
            <ul className="divide-y">
              {sessions.map((s) => <SessionRow key={s.session} entry={s} onOpen={onOpen} onDownloaded={onDownloaded} />)}
            </ul>
          </Card>
        ))}
      </PageBody>
    </div>
  )
}

function SessionRow({ entry: s, onOpen, onDownloaded }: {
  entry: HistoryEntry; onOpen: (session: string) => void; onDownloaded: (job: string) => void
}) {
  const [open, setOpen] = React.useState(false)
  const [note, setNote] = React.useState<{ text: string; folder?: string; bad?: boolean } | null>(null)
  const exportAs = (format: "bibtex" | "ris" | "doi") =>
    historyApi.export(s.session, format).then((r) => {
      const folder = r.file.replace(/[\\/][^\\/]+$/, "")
      setNote({ text: `已导出 ${r.count} 篇：${r.file}`, folder })
    }).catch((e) => setNote({ text: errorMessage(e), bad: true }))
  const watch = () =>
    watchesApi.add(s.session).then(() => setNote({ text: "已加入追踪，以后在「追踪」里检查新文献" }))
      .catch((e) => setNote({ text: errorMessage(e), bad: true }))
  const download = () =>
    downloadsApi.start(s.selected.flatMap((p) => (p.doi ? [{ ...p, doi: p.doi }] : [])), s.question || s.session, s.session)
      .then((j) => onDownloaded(j.job)).catch((e) => setNote({ text: errorMessage(e), bad: true }))
  return (
    <li>
      <div className="flex items-center gap-4 px-5 py-3">
        <button type="button" onClick={() => setOpen((o) => !o)} disabled={!s.selected.length}
          className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-default">
          {s.selected.length
            ? (open ? <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground" /> : <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" />)
            : <span className="size-4 shrink-0" />}
          <span className="shrink-0 text-sm tabular-nums">{(s.created || s.updated).slice(0, 16)}</span>
          <span className="shrink-0 rounded border px-1.5 text-xs text-muted-foreground">{MODE[s.mode] ?? s.mode}</span>
          <span className="truncate text-xs text-muted-foreground">
            已评分 {s.items} · 高 {s.by_relevance["高"] ?? 0} · 中 {s.by_relevance["中"] ?? 0}
          </span>
        </button>
        <span className={cn("shrink-0 text-sm tabular-nums", s.selected.length ? "font-medium" : "text-muted-foreground")}>
          待下载 {s.selected.length} 篇
        </span>
        <Button size="sm" variant="ghost" onClick={() => onOpen(s.session)}>打开</Button>
      </div>
      {open && (
        <div className="space-y-3 border-t bg-canvas/60 px-5 py-3">
          <PrismaLine session={s.session} onNote={setNote} />
          <ol className="space-y-1.5">
            {s.selected.map((p, i) => (
              <li key={p.doi ?? p.title} className="flex gap-3 text-sm">
                <span className="w-6 shrink-0 pt-px text-right text-xs text-muted-foreground tabular-nums">{i + 1}</span>
                <div className="min-w-0">
                  <div className="line-clamp-2">{p.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {[p.year, p.journal, p.journal_rank].filter(Boolean).join(" · ")}{p.doi ? ` · ${p.doi}` : ""}
                  </div>
                </div>
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">导出</span>
            <Button size="sm" variant="outline" onClick={() => exportAs("bibtex")}>BibTeX</Button>
            <Button size="sm" variant="outline" onClick={() => exportAs("ris")}>RIS</Button>
            <Button size="sm" variant="outline" onClick={() => exportAs("doi")}>DOI 列表</Button>
            <Button size="sm" variant="outline" className="ml-auto" onClick={watch}>追踪这个检索式</Button>
            <Button size="sm" onClick={download}>加入下载</Button>
          </div>
          {note && (
            <p className={cn("flex items-center gap-2 text-xs", note.bad ? "text-destructive" : "text-muted-foreground")}>
              <span className="min-w-0 truncate" title={note.text}>{note.text}</span>
              {note.folder && (
                <button type="button" className="flex shrink-0 items-center gap-1 text-primary hover:underline"
                  onClick={() => downloadsApi.open(note.folder!).catch(() => {})}>
                  <FolderOpenIcon className="size-3.5" />打开文件夹
                </button>
              )}
            </p>
          )}
        </div>
      )}
    </li>
  )
}

/**
 * Saved searches (定期追踪): each one is checked by hand for now; only papers it
 * has not shown before come back, so a topic can be followed without re-reading.
 */
function WatchesCard() {
  const [items, setItems] = React.useState<Watch[] | null>(null)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [note, setNote] = React.useState<{ text: string; bad?: boolean } | null>(null)
  const [fresh, setFresh] = React.useState<Record<string, SearchHit[]>>({})
  const load = React.useCallback(() => {
    watchesApi.list().then(setItems).catch((e) => setNote({ text: errorMessage(e), bad: true }))
  }, [])
  React.useEffect(() => { load() }, [load])
  const check = (w: Watch) => {
    setBusy(w.id)
    watchesApi.check(w.id)
      .then((r) => {
        setFresh((old) => ({ ...old, [w.id]: r.new }))
        setNote({ text: r.new.length ? `「${w.question || w.query}」找到 ${r.new.length} 篇新文献` : "没有新文献" })
        load()
      })
      .catch((e) => setNote({ text: errorMessage(e), bad: true }))
      .finally(() => setBusy(null))
  }
  const remove = (w: Watch) => {
    watchesApi.remove(w.id).then(load).catch((e) => setNote({ text: errorMessage(e), bad: true }))
  }
  const download = (w: Watch) => {
    const papers = (fresh[w.id] ?? []).filter((p) => p.doi).map((p) => ({ doi: p.doi as string, title: p.title, year: p.year, journal: p.journal }))
    if (!papers.length) return setNote({ text: "这些新文献没有 DOI，无法加入下载", bad: true })
    downloadsApi.start(papers, `追踪 · ${w.question || w.query}`)
      .then(() => setNote({ text: `${papers.length} 篇新文献已加入下载队列` }))
      .catch((e) => setNote({ text: errorMessage(e), bad: true }))
  }
  if (!items?.length) return null
  return (
    <Card>
      <div className="border-b px-5 py-3 text-sm font-medium">追踪</div>
      <ul className="divide-y">
        {items.map((w) => {
          const papers = fresh[w.id] ?? []
          return (
            <li key={w.id} className="px-5 py-3">
              <div className="flex items-center gap-4">
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-1 text-sm">{w.question || w.query}</div>
                  <div className="text-xs text-muted-foreground">
                    {w.query} · 已见 {w.seen_count} 篇 · {w.checked ? `上次检查 ${w.checked.slice(0, 16)}` : "还没检查过"}
                    {w.checked && ` · 新增 ${w.last_new}`}
                  </div>
                </div>
                <Button size="sm" variant="outline" disabled={busy === w.id} onClick={() => check(w)}>
                  {busy === w.id ? "正在检查…" : "检查"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => remove(w)}>删除</Button>
              </div>
              {papers.length > 0 && (
                <div className="mt-2 rounded-md border bg-background px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-xs font-medium text-muted-foreground">这次的新文献</div>
                    <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" onClick={() => download(w)}>加入下载</Button>
                  </div>
                  <ul className="mt-1 space-y-1">
                    {papers.slice(0, 20).map((p, i) => (
                      <li key={`${w.id}-${i}`} className="line-clamp-1 text-sm">
                        {p.title}
                        <span className="ml-2 text-xs text-muted-foreground">{[p.year, p.journal].filter(Boolean).join(" · ")}</span>
                      </li>
                    ))}
                  </ul>
                  {papers.length > 20 && <p className="mt-1 text-xs text-muted-foreground">还有 {papers.length - 20} 篇</p>}
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {note && <p className={cn("px-5 py-2 text-xs", note.bad ? "text-destructive" : "text-muted-foreground")}>{note.text}</p>}
    </Card>
  )
}

/** How the papers moved from search hits to picks, for the review's record. */
function PrismaLine({ session, onNote }: {
  session: string; onNote: (note: { text: string; folder?: string; bad?: boolean }) => void
}) {
  const [d, setD] = React.useState<PrismaSummary | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  React.useEffect(() => {
    historyApi.prisma(session).then(setD).catch((e) => setError(errorMessage(e)))
  }, [session])
  if (error) return <p className="text-xs text-destructive">{error}</p>
  if (!d) return <p className="text-xs text-muted-foreground">正在统计检索流程…</p>
  const counts: [string, number][] = [
    ["检索命中", d.identified],
    ["去重后", d.screened],
    ["纳入", d.included],
    ["排除", d.excluded],
    ["低相关未定", d.low_without_decision],
  ]
  const exportFlow = () =>
    historyApi.prismaExport(session).then((r) => {
      onNote({ text: `已导出检索流程：${r.file}`, folder: r.file.replace(/[\/][^\/]+$/, "") })
    }).catch((e) => onNote({ text: errorMessage(e), bad: true }))
  return (
    <div className="rounded-md border bg-background px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium text-muted-foreground">检索流程</div>
        <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={exportFlow}>导出流程</Button>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm tabular-nums">
        {counts.map(([label, n]) => (
          <span key={label} className="whitespace-nowrap">
            <span className="text-muted-foreground">{label}</span> {n}
          </span>
        ))}
      </div>
      {d.reasons.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-xs text-muted-foreground">
          {d.reasons.slice(0, 3).map((r) => (
            <li key={r.reason} className="truncate" title={r.reason}>{r.reason} × {r.count}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
