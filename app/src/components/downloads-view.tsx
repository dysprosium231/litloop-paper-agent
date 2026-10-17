import * as React from "react"
import { CheckCircle2Icon, CircleDashedIcon, FolderOpenIcon, InboxIcon, LoaderIcon, XCircleIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { SidebarContent, SidebarHeader } from "@/components/ui/sidebar"
import {
  downloadsApi,
  type DownloadItem,
  type DownloadJob,
  type DownloadJobEntry,
  type JobState,
  type LiveDownload,
  type PendingGroup,
} from "@/lib/api"
import { cn } from "@/lib/utils"

export const PENDING = "pending"

const STATE: Record<JobState, string> = {
  running: "下载中", done: "已完成", cancelled: "已取消", failed: "出错", interrupted: "已中断",
}

/** Jobs, papers waiting to be downloaded and the live report; polls faster while a job runs. */
export function useDownloads(active: boolean) {
  const [jobs, setJobs] = React.useState<DownloadJobEntry[]>([])
  const [running, setRunning] = React.useState<string | null>(null)
  const [live, setLive] = React.useState<LiveDownload | null>(null)
  const [pending, setPending] = React.useState<PendingGroup[]>([])
  const [error, setError] = React.useState<string | null>(null)
  const refresh = React.useCallback(async () => {
    try {
      const [l, p] = await Promise.all([downloadsApi.list(), downloadsApi.pending()])
      setJobs(l.jobs)
      setRunning(l.running)
      setLive(l.live)
      setPending(p)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }, [])
  React.useEffect(() => {
    if (!active) return
    refresh()
    const t = setInterval(refresh, running ? 1500 : 5000)
    return () => clearInterval(t)
  }, [active, running, refresh])
  return { jobs, running, live, pending, error, refresh }
}

/** Second column of the download section: what waits, then the jobs, newest first. */
export function DownloadsNav({ jobs, pending, current, onPick }: {
  jobs: DownloadJobEntry[]; pending: PendingGroup[]; current: string; onPick: (id: string) => void
}) {
  const waiting = pending.reduce((n, g) => n + g.papers.length, 0)
  return (
    <>
      <SidebarHeader className="border-b p-4">
        <div className="text-base font-medium text-foreground">下载</div>
      </SidebarHeader>
      <SidebarContent className="scrollable gap-0.5 p-2">
        <button type="button" onClick={() => onPick(PENDING)}
          className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-sidebar-accent",
            current === PENDING && "bg-sidebar-accent font-medium")}>
          <InboxIcon className="size-4 shrink-0" />
          <span className="flex-1">待下载</span>
          {waiting > 0 && <span className="rounded-full bg-primary px-1.5 text-xs font-medium text-primary-foreground">{waiting}</span>}
        </button>
        {jobs.length > 0 && <div className="px-3 pt-4 pb-1 text-xs text-muted-foreground">下载记录</div>}
        {jobs.map((j) => (
          <button key={j.job} type="button" onClick={() => onPick(j.job)}
            className={cn("flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left hover:bg-sidebar-accent",
              current === j.job && "bg-sidebar-accent")}>
            <span className={cn("line-clamp-2 text-sm", current === j.job && "font-medium")}>{j.title}</span>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              {j.state === "running" && <LoaderIcon className="size-3 animate-spin" />}
              {STATE[j.state]} · {j.done}/{j.total} 篇{j.failed ? ` · 失败 ${j.failed}` : ""} · {j.created.slice(5, 16)}
            </span>
          </button>
        ))}
      </SidebarContent>
    </>
  )
}

function Header({ title, meta, children }: { title: string; meta?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b px-6 py-4">
      <div className="min-w-0">
        <h2 className="line-clamp-2 text-lg font-semibold">{title}</h2>
        {meta && <div className="mt-0.5 text-xs text-muted-foreground">{meta}</div>}
      </div>
      <div className="flex shrink-0 gap-2">{children}</div>
    </div>
  )
}

const metaLine = (p: { journal?: string | null; year?: number | null }) => [p.year, p.journal].filter(Boolean).join(" · ")

/** Papers picked 选中下载 in screening, grouped by session, each group one click away from a job. */
export function PendingPane({ pending, busy, onStarted }: {
  pending: PendingGroup[]; busy: boolean; onStarted: (job: string) => void
}) {
  const [error, setError] = React.useState<string | null>(null)
  const start = async (papers: PendingGroup["papers"], title: string, session?: string) => {
    try {
      onStarted((await downloadsApi.start(papers, title, session)).job)
    } catch (e) {
      setError(String(e).replace(/^Error: \d+ /, "").replace(/^\{"detail":"(.*)"\}$/, "$1"))
    }
  }
  const all = pending.flatMap((g) => g.papers)
  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollable">
      <Header title="待下载" meta={all.length ? `${all.length} 篇，来自 ${pending.length} 次筛选` : undefined}>
        {pending.length > 1 && <Button size="sm" disabled={busy} onClick={() => start(all, `${all.length} 篇（${pending.length} 次筛选）`)}>全部下载</Button>}
      </Header>
      {error && <p className="mx-6 mt-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
      {busy && <p className="mx-6 mt-4 text-sm text-muted-foreground">正在下载，完成后才能开始新的下载。</p>}
      {pending.length === 0 && (
        <p className="px-6 py-10 text-center text-sm text-muted-foreground">在筛选页把文章标为「选中下载」，就会出现在这里。</p>
      )}
      {pending.map((g) => (
        <section key={g.session} className="border-b px-6 py-4">
          <div className="mb-2 flex items-center justify-between gap-4">
            <h3 className="min-w-0 truncate text-sm font-medium">{g.question || g.session}</h3>
            <Button size="sm" variant={pending.length > 1 ? "outline" : "default"} disabled={busy}
              onClick={() => start(g.papers, g.question || g.session, g.session)}>下载这 {g.papers.length} 篇</Button>
          </div>
          <ul className="space-y-1.5">
            {g.papers.map((p) => (
              <li key={p.doi} className="text-sm">
                <div className="line-clamp-2">{p.title || p.doi}</div>
                <div className="text-xs text-muted-foreground">{metaLine(p) || p.doi}</div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function StatusIcon({ item, running }: { item: DownloadItem; running: boolean }) {
  if (item.status === "done") return <CheckCircle2Icon className="size-4 text-emerald-600 dark:text-emerald-400" />
  if (item.status === "failed") return <XCircleIcon className="size-4 text-destructive" />
  return running ? <LoaderIcon className="size-4 animate-spin text-muted-foreground" /> : <CircleDashedIcon className="size-4 text-muted-foreground" />
}

/** One job: progress, what needs the user (captcha, sign-in), and every paper's result. */
export function JobPane({ id, live, onChanged, onStarted }: {
  id: string; live: LiveDownload | null; onChanged: () => void; onStarted: (job: string) => void
}) {
  const [job, setJob] = React.useState<DownloadJob | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const running = job?.state === "running"
  React.useEffect(() => {
    let alive = true
    const load = () => downloadsApi.job(id).then((j) => { if (alive) { setJob(j); setError(null) } }).catch((e) => alive && setError(String(e)))
    load()
    const t = setInterval(load, 1500)
    return () => { alive = false; clearInterval(t) }
  }, [id])
  const act = (f: () => Promise<unknown>) => f().then(onChanged).catch((e) => setError(String(e)))

  if (!job) return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">{error ?? "正在读取…"}</div>
  const done = job.items.filter((i) => i.status === "done").length
  const failed = job.items.filter((i) => i.status === "failed")
  const finished = done + failed.length
  const missing = job.items.filter((i) => i.status !== "done")  // failed, or never reached (cancelled, interrupted)
  const attention = running ? Object.values(live?.attention ?? {}) : []
  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollable">
      <Header title={job.title} meta={`${STATE[job.state]} · ${job.created}${job.finished ? ` → ${job.finished.slice(11)}` : ""}`}>
        {running && <Button size="sm" variant="outline" onClick={() => act(downloadsApi.cancel)}>取消</Button>}
        {!running && missing.length > 0 && (
          <Button size="sm" variant="outline" onClick={() => act(async () => onStarted((await downloadsApi.start(missing, `重试：${job.title}`, job.session ?? undefined)).job))}>
            重试未下载的 {missing.length} 篇
          </Button>
        )}
        {job.output_dir && done > 0 && (
          <Button size="sm" variant="outline" onClick={() => act(() => downloadsApi.open(job.output_dir))}><FolderOpenIcon />打开文件夹</Button>
        )}
      </Header>
      <div className="space-y-3 border-b px-6 py-4">
        <div className="flex items-baseline justify-between text-sm">
          <span>已下载 {done} 篇{failed.length ? `，失败 ${failed.length} 篇` : ""}，共 {job.items.length} 篇</span>
          {running && live?.current && <span className="max-w-[50%] truncate text-xs text-muted-foreground">正在处理 {live.current}</span>}
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${(finished / Math.max(1, job.items.length)) * 100}%` }} />
        </div>
        {attention.map((a, i) => (
          <p key={i} className="rounded-md bg-warning px-3 py-2 text-sm text-warning-foreground">{a.message}</p>
        ))}
        {job.error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{job.error}</p>}
        {job.state === "interrupted" && <p className="text-sm text-muted-foreground">app 关闭时这次下载还没完成；可以重试没下载到的文章。</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>
      <ul>
        {job.items.map((it) => (
          <li key={it.doi} className="flex items-start gap-3 border-b px-6 py-3">
            <span className="mt-0.5"><StatusIcon item={it} running={running} /></span>
            <div className="min-w-0 flex-1">
              <div className="line-clamp-2 text-sm">{it.title}</div>
              <div className="text-xs text-muted-foreground">
                {[metaLine(it), it.source && it.status === "done" ? `来源 ${it.source}` : null].filter(Boolean).join(" · ") || it.doi}
              </div>
              {it.status === "failed" && it.error && <div className="mt-0.5 line-clamp-2 text-xs text-destructive/90">{it.error}</div>}
            </div>
            {it.file && (
              <Button size="sm" variant="ghost" className="shrink-0" onClick={() => act(() => downloadsApi.open(it.file!))}>打开</Button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
