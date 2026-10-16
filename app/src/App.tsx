import * as React from "react"
import { ChevronDownIcon } from "lucide-react"

import { AppSidebar, type Section } from "@/components/app-sidebar"
import { PaperList, type PaperRow } from "@/components/paper-list"
import { SettingsNav, SettingsPane } from "@/components/settings-view"
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
  settingsApi,
  type SettingGroup,
} from "@/lib/api"

const short = (q: string) => (q.length > 28 ? q.slice(0, 28) + "…" : q)

// Width of the icon rail plus the paper list, which the user drags; remembered between runs.
const WIDTH_KEY = "litloop.listWidth"
const MIN_WIDTH = 300
const MAX_WIDTH = 900

function useStoredWidth(): [number, (w: number) => void] {
  const [width, setWidth] = React.useState(() => {
    try {
      const w = Number(localStorage.getItem(WIDTH_KEY))
      return w >= MIN_WIDTH && w <= MAX_WIDTH ? w : 420
    } catch {
      return 420
    }
  })
  const set = React.useCallback((w: number) => {
    const clamped = Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, w)))
    setWidth(clamped)
    try {
      localStorage.setItem(WIDTH_KEY, String(clamped))
    } catch {
      /* storage unavailable: the width just isn't remembered */
    }
  }, [])
  return [width, set]
}

/** A draggable line on the list's right edge; arrow keys also move it when focused. */
function ResizeHandle({ width, onWidth }: { width: number; onWidth: (w: number) => void }) {
  // While dragging, set the CSS variable directly and switch off the sidebar's width
  // transition: going through React state on every move (and the 200 ms transition)
  // made the line lag behind the pointer. The width is stored once, on release.
  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const wrapper = e.currentTarget.closest<HTMLElement>("[data-slot=sidebar-wrapper]")
    const x0 = e.clientX
    const w0 = width
    let w = w0
    let frame = 0
    const clamp = (v: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, v))
    const move = (ev: PointerEvent) => {
      w = clamp(w0 + ev.clientX - x0)
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => wrapper?.style.setProperty("--sidebar-width", `${w}px`))
    }
    const up = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
      document.documentElement.classList.remove("resizing")
      onWidth(w)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
    document.documentElement.classList.add("resizing")
  }
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="调节列表宽度"
      aria-valuenow={width}
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={MAX_WIDTH}
      tabIndex={0}
      onPointerDown={start}
      onDoubleClick={() => onWidth(420)}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.stopPropagation()
          e.preventDefault()
          onWidth(width + (e.key === "ArrowRight" ? 20 : -20))
        }
      }}
      className="absolute inset-y-0 left-0 z-20 hidden w-2 -translate-x-1/2 cursor-col-resize focus-visible:outline-none md:block after:absolute after:inset-y-0 after:left-1/2 after:w-px after:bg-transparent hover:after:bg-foreground/15 focus-visible:after:bg-foreground/25"
    />
  )
}

export default function App() {
  const [sessions, setSessions] = React.useState<SessionListEntry[]>([])
  const [sid, setSid] = React.useState<string | null>(null)
  const [record, setRecord] = React.useState<SessionRecord | null>(null)
  const [summary, setSummary] = React.useState<Summary | null>(null)
  const [progress, setProgress] = React.useState<Progress | null>(null)
  const [current, setCurrent] = React.useState<string | null>(null)
  const [onlyBatch, setOnlyBatch] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [listWidth, setListWidth] = useStoredWidth()
  const [section, setSection] = React.useState<Section>("screen")
  const [groups, setGroups] = React.useState<SettingGroup[]>([])
  const [settingsGroup, setSettingsGroup] = React.useState("sources")
  React.useEffect(() => {
    if (section === "settings") settingsApi.list().then(setGroups).catch((e) => setError(String(e)))
  }, [section])

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
      if (section !== "screen") return
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
  }, [papers, idx, rate, toggleSelect, section])

  const heading = summary && onlyBatch && summary.current_batch?.length
    ? `第 ${summary.batches.length} 批 · ${summary.current_batch.length} 篇`
    : `已评 ${record?.items.length ?? 0} 篇`

  return (
    <TooltipProvider>
      <SidebarProvider style={{ "--sidebar-width": `${listWidth}px` } as React.CSSProperties}>
        <AppSidebar section={section} onSection={setSection}>
          {section === "settings" ? (
            <SettingsNav groups={groups} current={settingsGroup} onPick={setSettingsGroup} />
          ) : section === "screen" ? (
            <PaperList rows={rows} current={paperKey(paper ?? {})} onPick={setCurrent} heading={heading}
              onlyBatch={onlyBatch} onOnlyBatch={setOnlyBatch} />
          ) : (
            <p className="p-4 text-sm text-muted-foreground">{section === "download" ? "下载队列" : "Zotero"}还在做。</p>
          )}
        </AppSidebar>
        <SidebarInset className="relative min-h-0 min-w-0">
          <ResizeHandle width={listWidth} onWidth={setListWidth} />
          {error && <p className="m-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
          {section === "settings" ? (
            <SettingsPane groups={groups} current={settingsGroup}
              onSaved={(f) => setGroups((gs) => gs.map((g) => ({ ...g, fields: g.fields.map((x) => (x.key === f.key ? f : x)) })))} />
          ) : (<>
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
          <ProgressStrip progress={progress} summary={summary} />
          <DetailPane paper={paper} edited={edited} onRate={rate} onSelect={toggleSelect} onExclude={exclude} />
          </>)}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
