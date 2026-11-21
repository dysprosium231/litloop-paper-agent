import * as React from "react"
import { BookAIcon, ChevronDownIcon, HistoryIcon, PlusIcon, Undo2Icon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

import { AppSidebar, RAIL_COLLAPSED, RAIL_OPEN, type Section } from "@/components/app-sidebar"
import { PaperList, type PaperRow, type QuickBatchKind } from "@/components/paper-list"
import { SettingsPage } from "@/components/settings-view"
import { DownloadsOverview, JobPane, OVERVIEW, useDownloads } from "@/components/downloads-view"
import { useZotero, ZoteroPage, type ZoteroView } from "@/components/zotero-view"
import { DetailPane, type Paper } from "@/components/detail-pane"
import { Onboarding } from "@/components/onboarding"
import { GlossaryPage } from "@/components/glossary-view"
import { HistoryPage } from "@/components/history-view"
import { SearchPage } from "@/components/search-view"
import { ProgressStrip } from "@/components/progress-strip"
import { useSystemNotices } from "@/hooks/use-system-notices"
import { onOpenUrl } from "@tauri-apps/plugin-deep-link"
import { listen as tauriListen } from "@tauri-apps/api/event"
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
  type BatchOp,
  type Item,
  type Progress,
  type Relevance,
  type SessionListEntry,
  type SessionRecord,
  type Summary,
  settingsApi,
  type SettingGroup,
  errorMessage,
  type TranslationInfo,
} from "@/lib/api"
import {
  EMPTY_FILTERS,
  collectJournals,
  filterPapers,
  itemToUndoOp,
  previousToUndoOps,
  type ScreeningFilters,
  yearBounds,
} from "@/lib/screening-filters"

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
  const [resumable, setResumable] = React.useState(false)
  const [progress, setProgress] = React.useState<Progress | null>(null)
  const [current, setCurrent] = React.useState<string | null>(null)
  const [onlyBatch, setOnlyBatch] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [widths, setWidth] = useStoredWidths()
  const [railOpen, setRailOpen] = useRailOpen()
  const railExtra = railOpen ? RAIL_OPEN - RAIL_COLLAPSED : 0
  const [section, setSection] = React.useState<Section>("screen")
  const [history, setHistory] = React.useState(false)  // the screening section's history page
  const [newSearch, setNewSearch] = React.useState(false)  // ... and its new-search page
  const [glossary, setGlossary] = React.useState(false)  // ... and its glossary page
  const fullPage = history || newSearch || glossary  // screening without its paper list
  // First run: the guide covers the window until it is finished or skipped.
  const [guide, setGuide] = React.useState<{ group: string; intro: string }[] | null>(null)
  React.useEffect(() => {
    settingsApi.onboarding().then((o) => { if (!o.done) setGuide(o.steps) }).catch(() => {})
  }, [])
  // litloop://session/<id> — an agent, a note or a web page can open one
  // screening session directly instead of asking the user to find it.
  const openLink = React.useCallback((url: string) => {
    const hit = /^litloop:\/\/session\/([^/?#]+)/.exec(url)
    if (!hit) return
    setSid(decodeURIComponent(hit[1]))
    setSection("screen")
    setHistory(true)
    setNewSearch(false)
    setGlossary(false)
  }, [])
  React.useEffect(() => {
    const stops: (() => void)[] = []
    onOpenUrl((urls) => urls.forEach(openLink))
      .then((unlisten) => stops.push(unlisten))
      .catch(() => {})  // not running under Tauri (vite preview), nothing to listen to
    // A link clicked while the app is open comes through the single-instance
    // plugin as an event instead of a fresh launch.
    tauriListen<string>("litloop-url", (event) => openLink(event.payload))
      .then((unlisten) => stops.push(unlisten))
      .catch(() => {})
    return () => stops.forEach((stop) => stop())
  }, [openLink])
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
  useSystemNotices()
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
    api.session(sid).then(({ record, summary, resumable: canResume }) => {
      setRecord(record)
      setSummary(summary)
      setResumable(Boolean(canResume))
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

  const [filters, setFilters] = React.useState<ScreeningFilters>(EMPTY_FILTERS)
  const [checked, setChecked] = React.useState<Set<string>>(() => new Set())
  const lastCheckedRef = React.useRef<string | null>(null)
  const [undoStack, setUndoStack] = React.useState<{ label: string; ops: BatchOp[] }[]>([])
  const [batchBusy, setBatchBusy] = React.useState(false)
  const [notice, setNotice] = React.useState<{ text: string; canUndo?: boolean } | null>(null)
  const noticeHoverRef = React.useRef(false)

  React.useEffect(() => {
    setFilters(EMPTY_FILTERS)
    setChecked(new Set())
    lastCheckedRef.current = null
    setUndoStack([])
    setNotice(null)
  }, [sid])

  React.useEffect(() => {
    if (!notice) return
    const timer = setInterval(() => {
      if (!noticeHoverRef.current) setNotice(null)
    }, 8000)
    return () => clearInterval(timer)
  }, [notice])

  // One list of papers: this batch (deep search) or everything rated so far.
  const items = React.useMemo(() => new Map((record?.items ?? []).map((it) => [paperKey(it), it])), [record])
  const papers: Paper[] = React.useMemo(() => {
    const batch = onlyBatch && summary?.current_batch?.length ? summary.current_batch : null
    const source = batch ?? record?.items ?? []
    return source.map((p) => ({ ...p, item: items.get(paperKey(p)) ?? null }))
  }, [onlyBatch, summary, record, items])
  const journals = React.useMemo(() => collectJournals(papers), [papers])
  const yearRange = React.useMemo(() => yearBounds(papers), [papers])
  const shown = React.useMemo(() => filterPapers(papers, filters), [papers, filters])
  const rows: PaperRow[] = React.useMemo(() => shown.map((p) => ({
    key: paperKey(p),
    title: p.title,
    meta: [p.year, p.journal].filter(Boolean).join(" · "),
    reason: p.item?.reason ?? "",
    rel: p.item ? rating(p.item) : null,
    edited: Boolean(p.item?.user_relevance),
    selected: p.item?.decision === "selected",
    rejected: p.item?.decision === "rejected",
    rejectReason: p.item?.reject_reason ?? "",
    hasItem: Boolean(p.item),
  })), [shown])

  React.useEffect(() => {
    const valid = new Set(papers.map((p) => paperKey(p)))
    setChecked((prev) => {
      let changed = false
      const next = new Set<string>()
      for (const k of prev) {
        if (valid.has(k)) next.add(k)
        else changed = true
      }
      return changed ? next : prev
    })
  }, [papers])

  const quickCounts = React.useMemo(() => {
    let high = 0
    let highMed = 0
    let irrelevant = 0
    let lowIrrelevant = 0
    for (const p of shown) {
      if (!p.item || p.item.decision) continue
      const r = rating(p.item)
      if (r === "高") { high += 1; highMed += 1 }
      else if (r === "中") { highMed += 1 }
      else if (r === "不相关") { irrelevant += 1; lowIrrelevant += 1 }
      else if (r === "低") { lowIrrelevant += 1 }
    }
    return { high, highMed, irrelevant, lowIrrelevant }
  }, [shown])

  const idx = Math.max(0, shown.findIndex((p) => paperKey(p) === current))
  const paper = shown[idx] ?? null

  React.useEffect(() => {
    if (shown.length && !shown.some((p) => paperKey(p) === current)) setCurrent(paperKey(shown[0]))
  }, [shown, current])

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

  const pushUndo = React.useCallback((label: string, ops: BatchOp[]) => {
    if (!ops.length) return
    setUndoStack((prev) => [...prev.slice(-19), { label, ops }])
  }, [])

  const showNotice = React.useCallback((text: string, canUndo = false) => {
    noticeHoverRef.current = false
    setNotice({ text, canUndo })
  }, [])

  const runBatch = React.useCallback(
    async (ops: BatchOp[], labelForCount: (n: number) => string, clearSelection = false) => {
      if (!sid || !ops.length || batchBusy) return
      setBatchBusy(true)
      try {
        const res = await api.batch(sid, ops)
        reload()
        if (res.updated > 0) {
          const label = labelForCount(res.updated)
          pushUndo(label, previousToUndoOps(res.previous))
          const text = res.skipped > 0 ? `${label}（跳过 ${res.skipped} 篇待评文献）` : label
          showNotice(text, true)
        } else if (res.skipped > 0) {
          showNotice(`已跳过 ${res.skipped} 篇待评文献，请先评分后再操作`, false)
        }
        if (clearSelection) {
          setChecked(new Set())
          lastCheckedRef.current = null
        }
      } catch (e) {
        setError(errorMessage(e))
      } finally {
        setBatchBusy(false)
      }
    },
    [sid, batchBusy, reload, pushUndo, showNotice],
  )

  const undo = React.useCallback(async () => {
    if (!sid || batchBusy || undoStack.length === 0) return
    const entry = undoStack[undoStack.length - 1]
    setBatchBusy(true)
    try {
      await api.batch(sid, entry.ops)
      setUndoStack((prev) => prev.slice(0, -1))
      reload()
      showNotice(`已撤销：${entry.label}`, false)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBatchBusy(false)
    }
  }, [sid, batchBusy, undoStack, reload, showNotice])

  const toggleCheck = React.useCallback((key: string, shiftKey: boolean) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (shiftKey && lastCheckedRef.current) {
        const keys = shown.map((p) => paperKey(p))
        const a = keys.indexOf(lastCheckedRef.current)
        const b = keys.indexOf(key)
        if (a !== -1 && b !== -1) {
          const [start, end] = a < b ? [a, b] : [b, a]
          const shouldSelect = !prev.has(key)
          for (let i = start; i <= end; i += 1) {
            if (shouldSelect) next.add(keys[i])
            else next.delete(keys[i])
          }
          return next
        }
      }
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
    lastCheckedRef.current = key
  }, [shown])

  const checkAll = React.useCallback(() => {
    setChecked((prev) => {
      const next = new Set(prev)
      for (const p of shown) next.add(paperKey(p))
      return next
    })
  }, [shown])

  const clearCheck = React.useCallback(() => {
    setChecked(new Set())
    lastCheckedRef.current = null
  }, [])

  const handleQuickAction = React.useCallback((kind: QuickBatchKind) => {
    const targets = shown.filter((p) => {
      if (!p.item || p.item.decision) return false
      const r = rating(p.item)
      if (kind === "accept_high") return r === "高"
      if (kind === "accept_high_med") return r === "高" || r === "中"
      if (kind === "reject_irrelevant") return r === "不相关"
      return r === "低" || r === "不相关"
    })
    if (!targets.length) return
    const decision = kind === "accept_high" || kind === "accept_high_med" ? "selected" : "rejected"
    const ops: BatchOp[] = targets.map((p) => ({
      doi: p.doi ?? null,
      title: p.title,
      decision,
    }))
    const labelForCount = (n: number) => (decision === "selected" ? `已将 ${n} 篇加入下载` : `已排除 ${n} 篇`)
    void runBatch(ops, labelForCount, false)
  }, [shown, runBatch])

  const handleBatchDecision = React.useCallback((decision: Item["decision"], reason?: string) => {
    const targets = papers.filter((p) => checked.has(paperKey(p)))
    if (!targets.length) return
    const ops: BatchOp[] = targets.map((p) => ({
      doi: p.doi ?? null,
      title: p.title,
      decision,
      ...(decision === "rejected" ? { reject_reason: reason ?? null } : {}),
    }))
    const labelForCount = (n: number) =>
      decision === "selected" ? `已将 ${n} 篇加入下载` : decision === "rejected" ? `已排除 ${n} 篇` : `已清除 ${n} 篇决定`
    void runBatch(ops, labelForCount, true)
  }, [papers, checked, runBatch])

  const handleBatchRate = React.useCallback((rel: Relevance | null) => {
    const targets = papers.filter((p) => checked.has(paperKey(p)))
    if (!targets.length) return
    const ops: BatchOp[] = targets.map((p) => ({
      doi: p.doi ?? null,
      title: p.title,
      ...(rel === null ? { clear_user_relevance: true } : { relevance: rel }),
    }))
    const labelForCount = (n: number) => (rel === null ? `已恢复 ${n} 篇 Agent 评分` : `已将 ${n} 篇评为「${rel}」`)
    void runBatch(ops, labelForCount, true)
  }, [papers, checked, runBatch])

  // A paper the agent has not rated yet (a batch the app served) can be rated first by the user.
  const rate = React.useCallback((rel: (typeof RELEVANCE)[number]) => {
    if (!sid || !paper) return
    const undoOp = itemToUndoOp(paper)
    const same = paper.item && rel === (paper.item.reason ? paper.item.relevance : rating(paper.item))
    api.rate(sid, paper, same ? null : rel).then(() => {
      pushUndo("修改评分", [undoOp])
      reload()
    }).catch((e) => setError(errorMessage(e)))
  }, [sid, paper, reload, pushUndo])
  const continueSearch = React.useCallback(() => {
    if (!sid) return
    api.next(sid).then(() => api.progress(sid)).then(setProgress).catch((e) => setError(errorMessage(e)))
  }, [sid])
  const resumeSearch = React.useCallback(() => {
    if (!sid) return
    api.resume(sid).then((r) => {
      if (r.state === "failed") {
        setError(r.error)
        return
      }
      setNewSearch(true)
    }).catch((e) => setError(errorMessage(e)))
  }, [sid])
  const toggleSelect = React.useCallback(() => {
    if (!sid || !paper?.item) return
    const undoOp = itemToUndoOp(paper)
    const nextDec = paper.item.decision === "selected" ? null : "selected"
    api.decide(sid, paper, nextDec).then(() => {
      pushUndo(nextDec === "selected" ? "加入下载" : "移出下载", [undoOp])
      reload()
    }).catch((e) => setError(errorMessage(e)))
  }, [sid, paper, reload, pushUndo])
  const exclude = React.useCallback((reason?: string) => {
    if (!sid || !paper?.item) return
    const undoOp = itemToUndoOp(paper)
    const nextDec = paper.item.decision === "rejected" ? null : "rejected"
    api.decide(sid, paper, nextDec, reason).then(() => {
      pushUndo(nextDec === "rejected" ? "排除文献" : "取消排除", [undoOp])
      reload()
    }).catch((e) => setError(errorMessage(e)))
  }, [sid, paper, reload, pushUndo])

  // Keyboard: ↑↓ move, 1-4 rate, space select, Ctrl+Z undo, Esc clear checks. Not while typing in a field.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (section !== "screen" || fullPage) return
      const t = e.target as HTMLElement
      if (t.closest("input, textarea, select, [contenteditable=true]")) return
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "z") {
        e.preventDefault()
        void undo()
        return
      }
      if (e.key === "Escape" && checked.size > 0) {
        e.preventDefault()
        clearCheck()
        return
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const next = shown[idx + (e.key === "ArrowDown" ? 1 : -1)]
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
  }, [shown, idx, rate, toggleSelect, section, fullPage, undo, checked.size, clearCheck])

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
      <SidebarProvider className="h-svh overflow-hidden" style={{ "--sidebar-width": `${(section === "screen" && !fullPage ? widths.screen : RAIL_COLLAPSED) + railExtra}px` } as React.CSSProperties}>
        <AppSidebar section={section} onSection={setSection} railOpen={railOpen} onRailOpen={setRailOpen}>
          {section === "screen" && !fullPage ? (
            <PaperList
              rows={rows}
              totalCount={papers.length}
              current={paperKey(paper ?? {})}
              onPick={setCurrent}
              heading={heading}
              onlyBatch={onlyBatch}
              onOnlyBatch={setOnlyBatch}
              filters={filters}
              onFilters={setFilters}
              journals={journals}
              yearRange={yearRange}
              checked={checked}
              onToggleCheck={toggleCheck}
              onCheckAll={checkAll}
              onClearCheck={clearCheck}
              quickCounts={quickCounts}
              busy={batchBusy}
              onQuickAction={handleQuickAction}
              onBatchDecision={handleBatchDecision}
              onBatchRate={handleBatchRate}
            />
          ) : null}
        </AppSidebar>
        <SidebarInset className="relative min-h-0 min-w-0">
          {section === "screen" && !fullPage && (
            <ResizeHandle key={section} width={widths[section]} min={WIDTHS[section].min} initial={WIDTHS[section].initial}
              offset={railExtra} onWidth={(w) => setWidth(section, w)} />
          )}
          {error && <p className="m-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
          {section === "zotero" ? (
            <ZoteroPage data={zotero.data} error={zotero.error} mode={zotero.mode} onModeChanged={zotero.loadMode}
              onChanged={zotero.refresh} onOpenJob={openJob} view={zoteroView} onView={setZoteroView} />
          ) : section === "download" ? (
            downloadView === OVERVIEW
              ? <DownloadsOverview jobs={downloads.jobs} pending={downloads.pending} outputDir={downloads.outputDir} external={downloads.external}
                  busy={Boolean(downloads.running)} running={downloads.running} onStarted={startedJob} onOpen={setDownloadView}
                  onChanged={downloads.refresh} />
              : <JobPane key={downloadView} id={downloadView} live={downloads.live} busy={Boolean(downloads.running)}
                  onChanged={downloads.refresh} onStarted={startedJob}
                  onBack={() => setDownloadView(OVERVIEW)} />
          ) : section === "settings" ? (
            <SettingsPage groups={groups} current={settingsGroup} onPick={setSettingsGroup} onGuide={openGuide}
              onSaved={(f) => setGroups((gs) => gs.map((g) => ({ ...g, fields: g.fields.map((x) => (x.key === f.key ? f : x)) })))} />
          ) : newSearch ? (
            <SearchPage onBack={() => { setNewSearch(false); reload() }}
              onOpen={(s) => {
                api.sessions().then(setSessions).catch(() => {})
                if (s === sid) reload()
                else setSid(s)
                setNewSearch(false)
              }} />
          ) : glossary ? (
            <GlossaryPage onBack={() => setGlossary(false)} />
          ) : history ? (
            <HistoryPage onBack={() => setHistory(false)}
              onOpen={(s) => { setSid(s); setHistory(false) }}
              onDownloaded={(job) => { setHistory(false); openJob(job) }} />
          ) : (<>
          <header className="sticky top-0 z-10 flex shrink-0 items-center gap-2 border-b bg-background px-4 py-3">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-2 data-vertical:h-4 data-vertical:self-auto" />
            <Breadcrumb className="min-w-0">
              <BreadcrumbList className="flex-nowrap">
                <BreadcrumbItem className="hidden shrink-0 whitespace-nowrap md:block">{record?.mode === "deep" ? "深度检索" : "快速检索"}</BreadcrumbItem>
                <BreadcrumbSeparator className="hidden md:block" />
                <BreadcrumbItem className="min-w-0">
                  <DropdownMenu>
                    <DropdownMenuTrigger className="flex min-w-0 items-center gap-1 rounded-md px-1 outline-none hover:bg-accent">
                      <BreadcrumbPage className="min-w-0 max-w-[32rem] truncate whitespace-nowrap">{record ? short(record.question) : "选择检索会话"}</BreadcrumbPage>
                      <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
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
            <div className="ml-auto flex shrink-0 items-center gap-1">
              {undoStack.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="shrink-0"
                  disabled={batchBusy}
                  onClick={() => void undo()}
                  title={`撤销：${undoStack[undoStack.length - 1].label}（Ctrl+Z）`}
                >
                  <Undo2Icon />撤销
                </Button>
              )}
              <Button size="sm" variant="ghost" className="shrink-0" onClick={() => setNewSearch(true)}>
                <PlusIcon />新建检索
              </Button>
              <Button size="sm" variant="ghost" className="shrink-0" onClick={() => setHistory(true)}>
                <HistoryIcon />筛选历史
              </Button>
              <Button size="sm" variant="ghost" className="shrink-0" onClick={() => setGlossary(true)}>
                <BookAIcon />术语表
              </Button>
            </div>
          </header>
          <ProgressStrip progress={progress} summary={summary} resumable={resumable} onContinue={continueSearch} onResume={resumeSearch} />
          {notice && (
            <div
              onMouseEnter={() => { noticeHoverRef.current = true }}
              onMouseLeave={() => { noticeHoverRef.current = false }}
              className="flex shrink-0 items-center justify-between gap-3 border-b bg-accent/40 px-6 py-2 text-sm"
            >
              <span className="min-w-0 truncate">{notice.text}</span>
              <div className="flex shrink-0 items-center gap-2">
                {notice.canUndo && undoStack.length > 0 && (
                  <Button size="sm" variant="outline" disabled={batchBusy} onClick={() => void undo()} className="h-7 px-2.5 text-xs">
                    撤销
                  </Button>
                )}
                <button
                  type="button"
                  aria-label="关闭提示"
                  onClick={() => setNotice(null)}
                  className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <XIcon className="size-3.5" />
                </button>
              </div>
            </div>
          )}
          <DetailPane
            paper={paper}
            emptyHint={papers.length > 0 && shown.length === 0 ? "当前筛选条件下没有匹配的文献" : undefined}
            onRate={rate}
            onSelect={toggleSelect}
            onExclude={exclude}
            translation={translation}
            onTranslate={translateAbstract}
          />
          </>)}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
