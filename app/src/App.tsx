import * as React from "react"
import { ChevronDownIcon, HistoryIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

import { AppSidebar, RAIL_COLLAPSED, RAIL_OPEN, type Section } from "@/components/app-sidebar"
import { PaperList, type PaperRow } from "@/components/paper-list"
import { SettingsPage } from "@/components/settings-view"
import { DownloadsOverview, JobPane, OVERVIEW, useDownloads } from "@/components/downloads-view"
import { useZotero, ZoteroPage, type ZoteroView } from "@/components/zotero-view"
import { DetailPane, type Paper } from "@/components/detail-pane"
import { Onboarding } from "@/components/onboarding"
import { HistoryPage } from "@/components/history-view"
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
  errorMessage,
  type TranslationInfo,
} from "@/lib/api"

const short = (q: string) => (q.length > 28 ? q.slice(0, 28) + "…" : q)

// Width of the collapsed icon rail plus each section's list, which the user drags; each
// section keeps its own width between runs. An open rail adds RAIL_OPEN - RAIL_COLLAPSED
// on top, so the list keeps its width.
type Resizable = "screen"
const WIDTHS: Record<Resizable, { min: number; initial: number }> = {
  screen: { min: 360, initial: 420 },  // 360 keeps the list header (title + 只看本批) whole
}
const MAX_WIDTH = 900
const widthKey = (s: Resizable) => (s === "screen" ? "litloop.listWidth" : `litloop.listWidth.${s}`)  // screen keeps its old key

function readWidth(s: Resizable): number {
  try {
    const w = Number(localStorage.getItem(widthKey(s)))
    return w >= WIDTHS[s].min && w <= MAX_WIDTH ? w : WIDTHS[s].initial
  } catch {
    return WIDTHS[s].initial
  }
}

function useStoredWidths(): [Record<Resizable, number>, (s: Resizable, w: number) => void] {
  const [widths, setWidths] = React.useState(() => ({
    screen: readWidth("screen"),
  }))
  const set = React.useCallback((s: Resizable, w: number) => {
    const clamped = Math.round(Math.min(MAX_WIDTH, Math.max(WIDTHS[s].min, w)))
    setWidths((all) => ({ ...all, [s]: clamped }))
    try {
      localStorage.setItem(widthKey(s), String(clamped))
    } catch {
      /* storage unavailable: the width just isn't remembered */
    }
  }, [])
  return [widths, set]
}

const RAIL_KEY = "litloop.railOpen"

function useRailOpen(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = React.useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) !== "0"
    } catch {
      return true
    }
  })
  const set = React.useCallback((v: boolean) => {
    setOpen(v)
    try {
      localStorage.setItem(RAIL_KEY, v ? "1" : "0")
    } catch {
      /* storage unavailable: the rail opens next time */
    }
  }, [])
  return [open, set]
}

/** A draggable line on the list's right edge; arrow keys also move it when focused. */
function ResizeHandle({ width, min, initial, offset, onWidth }: {
  width: number; min: number; initial: number; offset: number; onWidth: (w: number) => void
}) {
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
    const clamp = (v: number) => Math.min(MAX_WIDTH, Math.max(min, v))
    const move = (ev: PointerEvent) => {
      w = clamp(w0 + ev.clientX - x0)
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => wrapper?.style.setProperty("--sidebar-width", `${w + offset}px`))
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
      aria-valuemin={min}
      aria-valuemax={MAX_WIDTH}
      tabIndex={0}
      onPointerDown={start}
      onDoubleClick={() => onWidth(initial)}
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
  const [widths, setWidth] = useStoredWidths()
  const [railOpen, setRailOpen] = useRailOpen()
  const railExtra = railOpen ? RAIL_OPEN - RAIL_COLLAPSED : 0
  const [section, setSection] = React.useState<Section>("screen")
  const [history, setHistory] = React.useState(false)  // the screening section's history page
  // First run: the guide covers the window until it is finished or skipped.
  const [guide, setGuide] = React.useState<{ group: string; intro: string }[] | null>(null)
  React.useEffect(() => {
    settingsApi.onboarding().then((o) => { if (!o.done) setGuide(o.steps) }).catch(() => {})
  }, [])
  const openGuide = React.useCallback(() => {
    settingsApi.onboarding().then((o) => setGuide(o.steps)).catch((e) => setError(errorMessage(e)))
  }, [])
  // Only screening has a list column: switching sections changes the sidebar
  // width, and its transition (meant for collapsing) would slide. Skip it here.
  React.useLayoutEffect(() => {
    const root = document.documentElement
    root.classList.add("no-transition")
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("no-transition")))
    return () => {
      cancelAnimationFrame(frame)
      root.classList.remove("no-transition")
    }
  }, [section])
  // The settings page's quota view asks OpenAlex, which takes seconds: start early.
  React.useEffect(() => {
    if (section === "settings") settingsApi.openalex().catch(() => {})
  }, [section])
  const [groups, setGroups] = React.useState<SettingGroup[]>([])
  const [settingsGroup, setSettingsGroup] = React.useState("sources")
  const downloads = useDownloads(section === "download")
  const [downloadView, setDownloadView] = React.useState(OVERVIEW)
  const startedJob = (job: string) => { setDownloadView(job); downloads.refresh() }
  const zotero = useZotero(section === "zotero")
  const [zoteroView, setZoteroView] = React.useState<ZoteroView>("queue")
  const openJob = (job: string) => { setSection("download"); setDownloadView(job) }
  React.useEffect(() => {
    if (section === "settings") settingsApi.list().then(setGroups).catch((e) => setError(errorMessage(e)))
  }, [section])

  React.useEffect(() => {
    api.sessions().then((s) => {
      setSessions(s)
      setSid((cur) => cur ?? s[0]?.session ?? null)
    }).catch((e) => setError(errorMessage(e)))
  }, [])

  const reload = React.useCallback(() => {
    if (!sid) return
    api.session(sid).then(({ record, summary }) => {
      setRecord(record)
      setSummary(summary)
      setError(null)
    }).catch((e) => setError(errorMessage(e)))
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

  React.useEffect(() => {
    if (papers.length && !papers.some((p) => paperKey(p) === current)) setCurrent(paperKey(papers[0]))
  }, [papers, current])

  // Read again on each return to screening: the settings page may have changed it.
  const [translation, setTranslation] = React.useState<TranslationInfo | null>(null)
  React.useEffect(() => {
    if (section === "screen") api.translation().then(setTranslation).catch(() => setTranslation(null))
  }, [section])
  const translateAbstract = React.useCallback(async () => {
    if (!sid || !paper) return
    await api.translate(sid, paper)
    reload()
  }, [sid, paper, reload])

  // A paper the agent has not rated yet (a batch the app served) can be rated first by the user.
  const rate = React.useCallback((rel: (typeof RELEVANCE)[number]) => {
    if (!sid || !paper) return
    const same = paper.item && rel === (paper.item.reason ? paper.item.relevance : rating(paper.item))
    api.rate(sid, paper, same ? null : rel).then(reload).catch((e) => setError(errorMessage(e)))
  }, [sid, paper, reload])
  const continueSearch = React.useCallback(() => {
    if (!sid) return
    api.next(sid).then(() => api.progress(sid)).then(setProgress).catch((e) => setError(errorMessage(e)))
  }, [sid])
  const toggleSelect = React.useCallback(() => {
    if (!sid || !paper?.item) return
    api.decide(sid, paper, paper.item.decision === "selected" ? null : "selected").then(reload)
      .catch((e) => setError(errorMessage(e)))
  }, [sid, paper, reload])
  const exclude = React.useCallback(() => {
    if (!sid || !paper?.item) return
    api.decide(sid, paper, paper.item.decision === "rejected" ? null : "rejected").then(reload)
      .catch((e) => setError(errorMessage(e)))
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
    : `已评分 ${record?.items.length ?? 0} 篇`

  if (guide) {
    return <Onboarding steps={guide} onDone={() => {
      setGuide(null)
      if (section === "settings") settingsApi.list().then(setGroups).catch(() => {})
    }} />
  }

  return (
    <TooltipProvider>
      {/* The header's toggle hides the list; the rail then shows icons only (--sidebar-width-icon). */}
      <SidebarProvider className="h-svh overflow-hidden" style={{ "--sidebar-width": `${(section === "screen" && !history ? widths.screen : RAIL_COLLAPSED) + railExtra}px` } as React.CSSProperties}>
        <AppSidebar section={section} onSection={setSection} railOpen={railOpen} onRailOpen={setRailOpen}>
          {section === "screen" && !history ? (
            <PaperList rows={rows} current={paperKey(paper ?? {})} onPick={setCurrent} heading={heading}
              onlyBatch={onlyBatch} onOnlyBatch={setOnlyBatch} />
          ) : null}
        </AppSidebar>
        <SidebarInset className="relative min-h-0 min-w-0">
          {section === "screen" && !history && (
            <ResizeHandle key={section} width={widths[section]} min={WIDTHS[section].min} initial={WIDTHS[section].initial}
              offset={railExtra} onWidth={(w) => setWidth(section, w)} />
          )}
          {error && <p className="m-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
          {section === "zotero" ? (
            <ZoteroPage data={zotero.data} error={zotero.error} mode={zotero.mode} onModeChanged={zotero.loadMode}
              onChanged={zotero.refresh} onOpenJob={openJob} view={zoteroView} onView={setZoteroView} />
          ) : section === "download" ? (
            downloadView === OVERVIEW
              ? <DownloadsOverview jobs={downloads.jobs} pending={downloads.pending} outputDir={downloads.outputDir}
                  busy={Boolean(downloads.running)} running={downloads.running} onStarted={startedJob} onOpen={setDownloadView}
                  onChanged={downloads.refresh} />
              : <JobPane key={downloadView} id={downloadView} live={downloads.live} onChanged={downloads.refresh} onStarted={startedJob}
                  onBack={() => setDownloadView(OVERVIEW)} />
          ) : section === "settings" ? (
            <SettingsPage groups={groups} current={settingsGroup} onPick={setSettingsGroup} onGuide={openGuide}
              onSaved={(f) => setGroups((gs) => gs.map((g) => ({ ...g, fields: g.fields.map((x) => (x.key === f.key ? f : x)) })))} />
          ) : history ? (
            <HistoryPage onBack={() => setHistory(false)}
              onOpen={(s) => { setSid(s); setHistory(false) }}
              onDownloaded={(job) => { setHistory(false); openJob(job) }} />
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
                      <BreadcrumbPage>{record ? short(record.question) : "选择检索会话"}</BreadcrumbPage>
                      <ChevronDownIcon className="size-3.5 text-muted-foreground" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" className="max-w-md">
                      {sessions.map((s) => (
                        <DropdownMenuItem key={s.session} onSelect={() => setSid(s.session)}>
                          <div className="flex min-w-0 flex-col">
                            <span className="truncate">{s.question || s.session}</span>
                            <span className="text-xs text-muted-foreground">
                              {s.deep ? "深度检索" : "快速检索"} · 已评分 {s.items} · {s.updated}
                            </span>
                          </div>
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setHistory(true)}>
              <HistoryIcon />历史
            </Button>
          </header>
          <ProgressStrip progress={progress} summary={summary} onContinue={continueSearch} />
          <DetailPane paper={paper} onRate={rate} onSelect={toggleSelect} onExclude={exclude}
            translation={translation} onTranslate={translateAbstract} />
          </>)}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
