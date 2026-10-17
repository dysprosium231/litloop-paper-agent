import * as React from "react"
import { ChevronDownIcon, ChevronRightIcon, FolderOpenIcon } from "lucide-react"

import { Card, Empty, PageBody, PageHeader } from "@/components/page"
import { Button } from "@/components/ui/button"
import { downloadsApi, errorMessage, historyApi, type HistoryEntry } from "@/lib/api"
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
        {entries && groups.length === 0 && <Card><Empty title="暂无检索记录" /></Card>}
        {!entries && !error && <p className="text-sm text-muted-foreground">正在读取…</p>}
        {groups.map(([question, sessions]) => (
          <Card key={question}>
            <div className="border-b px-5 py-3">
              <h2 className="line-clamp-2 text-[15px] font-semibold">{question}</h2>
              <div className="mt-0.5 text-xs text-muted-foreground">{sessions.length} 次检索</div>
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
            已评 {s.items} · 高 {s.by_relevance["高"] ?? 0} · 中 {s.by_relevance["中"] ?? 0}
          </span>
        </button>
        <span className={cn("shrink-0 text-sm tabular-nums", s.selected.length ? "font-medium" : "text-muted-foreground")}>
          已选 {s.selected.length} 篇
        </span>
        <Button size="sm" variant="ghost" onClick={() => onOpen(s.session)}>打开</Button>
      </div>
      {open && (
        <div className="space-y-3 border-t bg-canvas/60 px-5 py-3">
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
            <Button size="sm" className="ml-auto" onClick={download}>加入下载</Button>
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
