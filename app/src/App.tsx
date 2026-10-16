import * as React from "react"
import { ChevronDownIcon } from "lucide-react"

import { AppSidebar, type PaperRow } from "@/components/app-sidebar"
import { DetailPane, type Paper } from "@/components/detail-pane"
import { ProgressStrip } from "@/components/progress-strip"
import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  api,
  paperKey,
  rating,
  RELEVANCE,
  type Progress,
  type SessionListEntry,
  type SessionRecord,
  type Summary,
} from "@/lib/api"

const short = (q: string) => (q.length > 28 ? q.slice(0, 28) + "…" : q)

export default function App() {
  const [sessions, setSessions] = React.useState<SessionListEntry[]>([])
  const [sid, setSid] = React.useState<string | null>(null)
  const [record, setRecord] = React.useState<SessionRecord | null>(null)
  const [summary, setSummary] = React.useState<Summary | null>(null)
  const [progress, setProgress] = React.useState<Progress | null>(null)
  const [current, setCurrent] = React.useState<string | null>(null)
  const [onlyBatch, setOnlyBatch] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    api.sessions().then((s) => {
      setSessions(s)
      setSid((cur) => cur ?? s[0]?.session ?? null)
    }).catch((e) => setError(String(e)))
  }, [])

  const reload = React.useCallback(() => {
    if (!sid) return
    api.session(sid).then(({ record, summary }) => {
      setRecord(record)
      setSummary(summary)
      setError(null)
    }).catch((e) => setError(String(e)))
  }, [sid])
  React.useEffect(reload, [reload])

  // Poll the running round; when it finishes, the next batch is ready: reload.
  React.useEffect(() => {
    if (!sid) return
    let last: Progress["state"] | undefined
    const tick = () => api.progress(sid).then((p) => {
      setProgress(p)
      if (last === "running" && p?.state !== "running") reload()
      last = p?.state
    }).catch(() => {})
    tick()
    const t = setInterval(tick, 1500)
    return () => clearInterval(t)
  }, [sid, reload])

  // One list of papers: this batch (deep search) or everything rated so far.
  const items = React.useMemo(() => new Map((record?.items ?? []).map((it) => [paperKey(it), it])), [record])
  const papers: Paper[] = React.useMemo(() => {
    const batch = onlyBatch && summary?.current_batch?.length ? summary.current_batch : null
    const source = batch ?? record?.items ?? []
    return source.map((p) => ({ ...p, item: items.get(paperKey(p)) ?? null }))
  }, [onlyBatch, summary, record, items])
  const rows: PaperRow[] = papers.map((p) => ({
    key: paperKey(p),
    title: p.title,
    meta: [p.year, p.journal].filter(Boolean).join(" · "),
    reason: p.item?.reason ?? "",
    rel: p.item ? rating(p.item) : null,
    edited: Boolean(p.item?.user_relevance),
    selected: p.item?.decision === "selected",
  }))
  const idx = Math.max(0, papers.findIndex((p) => paperKey(p) === current))
  const paper = papers[idx] ?? null
  const edited = (record?.items ?? []).filter((it) => it.user_relevance).length

  React.useEffect(() => {
    if (papers.length && !papers.some((p) => paperKey(p) === current)) setCurrent(paperKey(papers[0]))
  }, [papers, current])

  const rate = React.useCallback((rel: (typeof RELEVANCE)[number]) => {
    if (!sid || !paper?.item) return
    api.rate(sid, paper, rel === paper.item.relevance ? null : rel).then(reload).catch((e) => setError(String(e)))
  }, [sid, paper, reload])
  const toggleSelect = React.useCallback(() => {
    if (!sid || !paper?.item) return
    api.decide(sid, paper, paper.item.decision === "selected" ? null : "selected").then(reload)
      .catch((e) => setError(String(e)))
  }, [sid, paper, reload])
  const exclude = React.useCallback(() => {
    if (!sid || !paper?.item) return
    api.decide(sid, paper, paper.item.decision === "rejected" ? null : "rejected").then(reload)
      .catch((e) => setError(String(e)))
  }, [sid, paper, reload])

  // Keyboard: ↑↓ move, 1-4 rate, space select. Not while typing in a field.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.closest("input, textarea, [contenteditable=true]")) return
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const next = papers[idx + (e.key === "ArrowDown" ? 1 : -1)]
        if (next) { setCurrent(paperKey(next)); e.preventDefault() }
      } else if (["1", "2", "3", "4"].includes(e.key)) {
        rate(RELEVANCE[Number(e.key) - 1])
      } else if (e.key === " ") {
        toggleSelect()
        e.preventDefault()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [papers, idx, rate, toggleSelect])

  const heading = summary && onlyBatch && summary.current_batch?.length
    ? `第 ${summary.batches.length} 批 · ${summary.current_batch.length} 篇`
    : `已评 ${record?.items.length ?? 0} 篇`

  return (
    <TooltipProvider>
      <SidebarProvider style={{ "--sidebar-width": "380px" } as React.CSSProperties}>
        <AppSidebar rows={rows} current={paperKey(paper ?? {})} onPick={setCurrent} heading={heading}
          onlyBatch={onlyBatch} onOnlyBatch={setOnlyBatch} />
        <SidebarInset className="min-h-0">
          <header className="sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b bg-background px-4 py-3">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-2 data-vertical:h-4 data-vertical:self-auto" />
            <Breadcrumb>
              <BreadcrumbList>
                <BreadcrumbItem className="hidden md:block">{record?.mode === "deep" ? "深度检索" : "快速检索"}</BreadcrumbItem>
                <BreadcrumbSeparator className="hidden md:block" />
                <BreadcrumbItem>
                  <DropdownMenu>
                    <DropdownMenuTrigger className="flex items-center gap-1 rounded-md px-1 outline-none hover:bg-accent">
                      <BreadcrumbPage>{record ? short(record.question) : "选择会话"}</BreadcrumbPage>
                      <ChevronDownIcon className="size-3.5 text-muted-foreground" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" className="max-w-md">
                      {sessions.map((s) => (
                        <DropdownMenuItem key={s.session} onSelect={() => setSid(s.session)}>
                          <div className="flex min-w-0 flex-col">
                            <span className="truncate">{s.question || s.session}</span>
                            <span className="text-xs text-muted-foreground">
                              {s.deep ? "深度检索" : "快速检索"} · 已评 {s.items} · {s.updated}
                            </span>
                          </div>
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
          </header>
          {error && <p className="m-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
          <ProgressStrip progress={progress} summary={summary} />
          <DetailPane paper={paper} edited={edited} onRate={rate} onSelect={toggleSelect} onExclude={exclude} />
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
