import * as React from "react"
import { CheckCircle2Icon, ChevronRightIcon, CircleDashedIcon, FolderOpenIcon, LoaderIcon, XCircleIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, Empty, PageBody, PageHeader, SectionTitle } from "@/components/page"
import {
  downloadsApi,
  type DownloadItem,
  type DownloadJob,
  type DownloadJobEntry,
  type JobState,
  type LiveDownload,
  type PendingGroup,
  type ZoteroCollection,
  errorMessage,
} from "@/lib/api"
import { cn } from "@/lib/utils"

export const OVERVIEW = "overview"

const STATE: Record<JobState, string> = {
  running: "进行中", done: "已完成", cancelled: "已取消", failed: "失败", interrupted: "已中断",
}

/** Jobs, papers waiting to be downloaded and the live report; polls faster while a job runs. */
export function useDownloads(active: boolean) {
  const [jobs, setJobs] = React.useState<DownloadJobEntry[]>([])
  const [running, setRunning] = React.useState<string | null>(null)
  const [live, setLive] = React.useState<LiveDownload | null>(null)
  const [pending, setPending] = React.useState<PendingGroup[]>([])
  const [outputDir, setOutputDir] = React.useState("")
  const [error, setError] = React.useState<string | null>(null)
  const refresh = React.useCallback(async () => {
    try {
      const [l, p] = await Promise.all([downloadsApi.list(), downloadsApi.pending()])
      setJobs(l.jobs)
      setRunning(l.running)
      setLive(l.live)
      setOutputDir(l.output_dir)
      setPending(p)
      setError(null)
    } catch (e) {
      setError(errorMessage(e))
    }
  }, [])
  React.useEffect(() => {
    if (!active) return
    refresh()
    const t = setInterval(refresh, running ? 1500 : 5000)
    return () => clearInterval(t)
  }, [active, running, refresh])
  return { jobs, running, live, pending, outputDir, error, refresh }
}

/** Second column of the download section: what waits, then the jobs, newest first. */
/** "下载 › <job>" in the page header; the first part goes back to the overview. */
function Crumb({ onBack, title }: { onBack: () => void; title?: string }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <button type="button" onClick={onBack} className="shrink-0 text-muted-foreground hover:text-foreground">下载</button>
      {title && <><ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" /><span className="truncate">{title}</span></>}
    </span>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "bad" }) {
  return (
    <Card className="px-5 py-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone === "bad" && value > 0 && "text-destructive")}>
        {value.toLocaleString()}<span className="ml-1 text-sm font-normal text-muted-foreground">篇</span>
      </div>
    </Card>
  )
}

const metaLine = (p: { journal?: string | null; year?: number | null }) => [p.year, p.journal].filter(Boolean).join(" · ")

type Filter = "all" | "failed" | "unfinished"
const FILTERS: { id: Filter; label: string; keep: (j: DownloadJobEntry) => boolean }[] = [
  { id: "all", label: "全部", keep: () => true },
  { id: "failed", label: "有失败", keep: (j) => j.failed > 0 },
  { id: "unfinished", label: "未完成", keep: (j) => j.state !== "done" },
]

/**
 * The downloads page: totals and the save folder, the papers picked 选中下载 in
 * screening (grouped by session, one click from a job), then the job history.
 */
export function DownloadsOverview({ jobs, pending, outputDir, busy, running, onStarted, onOpen, onChanged }: {
  jobs: DownloadJobEntry[]; pending: PendingGroup[]; outputDir: string; busy: boolean; running: string | null
  onStarted: (job: string) => void; onOpen: (job: string) => void; onChanged: () => void
}) {
  const [error, setError] = React.useState<string | null>(null)
  const [filter, setFilter] = React.useState<Filter>("all")
  const start = async (papers: PendingGroup["papers"], title: string, session?: string) => {
    try {
      onStarted((await downloadsApi.start(papers, title, session)).job)
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  const openFolder = () => {
    setError(null)
    downloadsApi.open(outputDir).catch((e) => setError(errorMessage(e)))
  }
  const all = pending.flatMap((g) => g.papers)
  const done = jobs.reduce((n, j) => n + j.done, 0)
  const failed = jobs.reduce((n, j) => n + j.failed, 0)
  const shown = jobs.filter(FILTERS.find((f) => f.id === filter)!.keep)
  const current = running ? jobs.find((j) => j.job === running) : undefined
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="下载">
        {outputDir && (
          <Button size="sm" variant="outline" onClick={openFolder} title={outputDir}>
            <FolderOpenIcon />打开保存位置
          </Button>
        )}
      </PageHeader>
      <PageBody wide>
        {error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        {current && (
          <Card className="flex items-center gap-3 border-primary/30 bg-primary/5 px-5 py-3">
            <LoaderIcon className="size-4 shrink-0 animate-spin text-primary" />
            <span className="min-w-0 flex-1 truncate text-sm">
              正在下载：{current.title}
              <span className="ml-2 text-muted-foreground tabular-nums">{current.done + current.failed} / {current.total}</span>
            </span>
            <Button size="sm" variant="ghost" onClick={() => onOpen(current.job)}>查看</Button>
            <Button size="sm" variant="outline" onClick={() => downloadsApi.cancel().then(onChanged).catch((e) => setError(errorMessage(e)))}>取消下载</Button>
          </Card>
        )}
        <div className="grid grid-cols-3 gap-4">
          <Stat label="已下载" value={done} />
          <Stat label="失败" value={failed} tone="bad" />
          <Stat label="待下载" value={all.length} />
        </div>

        <section>
          <SectionTitle title="下载队列" note={all.length ? `${all.length} 篇 · ${pending.length} 个检索会话` : undefined}>
            {pending.length > 1 && (
              <Button size="sm" disabled={busy} onClick={() => start(all, `${pending.length} 个检索会话 · ${all.length} 篇`)}>全部下载</Button>
            )}
          </SectionTitle>
          {busy && <p className="mb-3 text-sm text-muted-foreground">当前有下载任务进行中。</p>}
          <Card className="divide-y">
            {pending.length === 0 && <Empty title="暂无待下载文献" note="在筛选中加入下载的文献将显示在此处" />}
            {pending.map((g) => (
              <div key={g.session} className="px-5 py-4">
                <div className="mb-2 flex items-center justify-between gap-4">
                  <h3 className="min-w-0 truncate text-sm font-medium">{g.question || g.session}</h3>
                  <Button size="sm" variant={pending.length > 1 ? "outline" : "default"} disabled={busy}
                    onClick={() => start(g.papers, g.question || g.session, g.session)}>开始下载</Button>
                </div>
                <ul className="space-y-1.5">
                  {g.papers.map((p, i) => (
                    <li key={p.doi} className="flex gap-3 text-sm">
                      <span className="w-6 shrink-0 pt-px text-right text-xs text-muted-foreground tabular-nums">{i + 1}</span>
                      <div className="min-w-0">
                        <div className="line-clamp-2">{p.title || p.doi}</div>
                        <div className="text-xs text-muted-foreground">{metaLine(p) || p.doi}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </Card>
        </section>

        <section>
          <SectionTitle title="历史记录">
            {jobs.length > 0 && FILTERS.map((f) => (
              <button key={f.id} type="button" onClick={() => setFilter(f.id)}
                className={cn("rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground",
                  filter === f.id && "bg-background font-medium text-foreground shadow-xs ring-1 ring-border")}>
                {f.label}
              </button>
            ))}
          </SectionTitle>
          <Card>
            {jobs.length === 0 && <Empty title="暂无下载记录" />}
            {jobs.length > 0 && shown.length === 0 && <Empty title="无符合条件的记录" />}
            {shown.length > 0 && (
              <ul className="divide-y">
                {shown.map((j) => (
                  <li key={j.job}>
                    <button type="button" onClick={() => onOpen(j.job)}
                      className="flex w-full items-center gap-4 px-5 py-3 text-left hover:bg-accent/60">
                      <span className="min-w-0 flex-1 truncate text-sm">{j.title}</span>
                      <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
                        {j.state === "running" && <LoaderIcon className="size-3 animate-spin" />}
                        {STATE[j.state]}
                      </span>
                      <span className="w-24 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
                        {j.done}/{j.total}
                        {j.failed ? <span className="text-destructive"> · 失败 {j.failed}</span> : null}
                      </span>
                      <span className="w-24 shrink-0 text-right text-xs text-muted-foreground tabular-nums">{j.created.slice(5, 16)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </section>
      </PageBody>
    </div>
  )
}

const ZOTERO: Record<NonNullable<DownloadItem["zotero"]>["status"], string> = {
  pushing: "Zotero：推送中", added: "Zotero：已添加", reused: "Zotero：已存在", no_pdf: "Zotero：附件缺失", failed: "Zotero：推送失败",
}

/** Push the job's downloaded papers to a Zotero collection (an existing one or a new path). */
function ZoteroPanel({ job, onPushed }: { job: DownloadJob; onPushed: () => void }) {
  const [open, setOpen] = React.useState(false)
  const [target, setTarget] = React.useState(job.zotero_default)
  const [collections, setCollections] = React.useState<ZoteroCollection[] | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const z = job.zotero
  const waiting = job.items.filter((i) => i.status === "done" && i.file && !["added", "reused"].includes(i.zotero?.status ?? "")).length
  const pushed = job.items.filter((i) => ["added", "reused"].includes(i.zotero?.status ?? "")).length
  const show = () => {
    setOpen(true)
    setError(null)
    downloadsApi.collections().then(setCollections).catch(() => setCollections([]))
  }
  const push = () => downloadsApi.push(job.job, target).then(() => { setOpen(false); onPushed() })
    .catch((e) => setError(errorMessage(e)))
  if (z?.state === "running") {
    const doneNow = job.items.filter((i) => i.zotero && i.zotero.status !== "pushing").length
    return <p className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderIcon className="size-3.5 animate-spin" />正在推送到「{z.collection}」 · 已处理 {doneNow}</p>
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4 text-sm">
        <span className="min-w-0 truncate text-muted-foreground">
          {pushed > 0 ? `已推送到「${z?.collection ?? ""}」 · ${pushed} 篇` : "未推送到 Zotero"}
          {waiting > 0 && pushed > 0 ? ` · 待推送 ${waiting}` : ""}
        </span>
        {waiting > 0 && !open && <Button size="sm" variant="outline" onClick={show}>推送到 Zotero</Button>}
      </div>
      {(z?.state === "failed" || z?.state === "interrupted") && z.error && <p className="text-sm text-destructive" title={z.error}>{z.error_text ?? z.error}</p>}
      {open && (
        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); push() }}>
          <Input list="zotero-collections" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="目标分类"
            placeholder="目标分类，以 / 分隔层级" className="flex-1" />
          <datalist id="zotero-collections">{collections?.map((c) => <option key={c.key} value={c.path} />)}</datalist>
          <Button type="submit" size="sm" disabled={!target.trim()}>推送</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>取消</Button>
        </form>
      )}
      {open && collections?.length === 0 && <p className="text-xs text-muted-foreground">无法读取 Zotero 分类：Zotero 未运行。启动 Zotero 后重试，或直接输入分类路径（不存在的分类会自动创建）。</p>}
      {open && !!collections?.length && <p className="text-xs text-muted-foreground">可选择已有分类，或输入新路径（不存在的分类会自动创建）。</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  )
}

function StatusIcon({ item, running }: { item: DownloadItem; running: boolean }) {
  if (item.status === "done") return <CheckCircle2Icon className="size-4 text-emerald-600 dark:text-emerald-400" />
  if (item.status === "failed") return <XCircleIcon className="size-4 text-destructive" />
  return running ? <LoaderIcon className="size-4 animate-spin text-muted-foreground" /> : <CircleDashedIcon className="size-4 text-muted-foreground" />
}

/** One job: progress, what needs the user (captcha, sign-in), and every paper's result. */
export function JobPane({ id, live, onChanged, onStarted, onBack }: {
  id: string; live: LiveDownload | null; onChanged: () => void; onStarted: (job: string) => void
  onBack: () => void
}) {
  const [job, setJob] = React.useState<DownloadJob | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const running = job?.state === "running"
  React.useEffect(() => {
    let alive = true
    const load = () => downloadsApi.job(id).then((j) => { if (alive) { setJob(j); setError(null) } }).catch((e) => alive && setError(errorMessage(e)))
    load()
    const t = setInterval(load, 1500)
    return () => { alive = false; clearInterval(t) }
  }, [id])
  const act = (f: () => Promise<unknown>) => f().then(onChanged).catch((e) => setError(errorMessage(e)))

  if (!job) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <PageHeader title={<Crumb onBack={onBack} />} />
        <div className="flex flex-1 items-center justify-center bg-canvas text-sm text-muted-foreground">{error ?? "正在加载…"}</div>
      </div>
    )
  }
  const done = job.items.filter((i) => i.status === "done").length
  const failed = job.items.filter((i) => i.status === "failed")
  const finished = done + failed.length
  const missing = job.items.filter((i) => i.status !== "done")  // failed, or never reached (cancelled, interrupted)
  const attention = running ? Object.values(live?.attention ?? {}) : []
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={<Crumb onBack={onBack} title={job.title} />}>
        {running && <Button size="sm" variant="outline" onClick={() => act(downloadsApi.cancel)}>取消下载</Button>}
        {!running && missing.length > 0 && (
          <Button size="sm" variant="outline" onClick={() => act(async () => onStarted((await downloadsApi.start(missing, `重试 · ${job.title}`, job.session ?? undefined)).job))}>
            重试未完成项（{missing.length}）
          </Button>
        )}
        {job.output_dir && done > 0 && (
          <Button size="sm" variant="outline" onClick={() => act(() => downloadsApi.open(job.output_dir))}><FolderOpenIcon />打开文件夹</Button>
        )}
      </PageHeader>
      <PageBody wide>
      <Card className="space-y-3 p-5">
        <div className="flex items-baseline justify-between gap-4 text-sm">
          <span>
            <span className="font-medium">{STATE[job.state]}</span>
            <span className="text-muted-foreground"> · 已完成 {done}{failed.length ? ` · 失败 ${failed.length}` : ""} · 共 {job.items.length}</span>
          </span>
          {running && live?.current
            ? <span className="max-w-[50%] truncate text-xs text-muted-foreground" title={live.current}>
                正在处理：{job.items.find((i) => i.doi === live.current?.toLowerCase())?.title ?? live.current}
              </span>
            : <span className="text-xs text-muted-foreground tabular-nums">{job.created}{job.finished ? `–${job.finished.slice(11)}` : ""}</span>}
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${(finished / Math.max(1, job.items.length)) * 100}%` }} />
        </div>
        {attention.map((a, i) => (
          <p key={i} className="rounded-md bg-warning px-3 py-2 text-sm text-warning-foreground">{a.message}</p>
        ))}
        {job.error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive" title={job.error}>{job.error_text ?? job.error}</p>}
        {job.state === "interrupted" && <p className="text-sm text-muted-foreground">下载任务未完成时应用已退出，可重试未完成项。</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}
        {done > 0 && <ZoteroPanel job={job} onPushed={onChanged} />}
      </Card>
      <Card>
      <ul className="divide-y">
        {job.items.map((it, i) => (
          <li key={it.doi} className="flex items-start gap-3 px-5 py-3">
            <span className="w-6 shrink-0 pt-0.5 text-right text-xs text-muted-foreground tabular-nums">{i + 1}</span>
            <span className="mt-0.5"><StatusIcon item={it} running={running} /></span>
            <div className="min-w-0 flex-1">
              <div className="line-clamp-2 text-sm">{it.title}</div>
              <div className="text-xs text-muted-foreground">
                {[metaLine(it), it.source && it.status === "done" ? `来源：${it.source}` : null].filter(Boolean).join(" · ") || it.doi}
              </div>
              {it.status === "waiting" && running && (it.stage || it.retrying) && (
                <div className="mt-0.5 flex items-center gap-1.5 text-xs text-primary">
                  <LoaderIcon className="size-3 animate-spin" />{it.stage ?? "换渠道重试中"}
                </div>
              )}
              {it.status === "failed" && it.error && <div className="mt-0.5 line-clamp-2 text-xs text-destructive/90" title={it.error}>{it.error_text ?? it.error}</div>}
              {it.zotero && (
                <div className={cn("mt-0.5 text-xs", it.zotero.status === "failed" ? "text-destructive/90" : it.zotero.status === "no_pdf" ? "text-warning-foreground" : "text-muted-foreground")}
                  title={it.zotero.error ?? undefined}>
                  {ZOTERO[it.zotero.status]}{it.zotero.status === "failed" && it.zotero.error ? `：${it.zotero.error_text ?? it.zotero.error}` : ""}
                </div>
              )}
            </div>
            {it.file && (
              <Button size="sm" variant="ghost" className="shrink-0" onClick={() => act(() => downloadsApi.open(it.file!))}>打开</Button>
            )}
          </li>
        ))}
      </ul>
      </Card>
      </PageBody>
    </div>
  )
}
