// Talks to the Python backend (litloop.app_server). Field meanings: docs/APP_DATA.md.
import { invoke } from "@tauri-apps/api/core"

export const RELEVANCE = ["高", "中", "低", "不相关"] as const
export type Relevance = (typeof RELEVANCE)[number]

export interface Item {
  n: number
  doi?: string
  title: string
  year?: number | string
  journal?: string
  journal_rank?: string
  authors?: string[]
  cited_by_count?: number
  abstract?: string
  abstract_missing?: boolean
  /** Chinese abstract (translate.py, or the agent). */
  abstract_zh?: string
  retracted?: boolean | null
  concern?: boolean | null
  relevance: Relevance
  reason: string
  user_relevance?: Relevance
  decision?: "selected" | "rejected" | null
}

export interface BatchPaper {
  n: number
  doi?: string
  title: string
  year?: number | string
  journal?: string
  journal_rank?: string | null
  authors?: string[]
  cited_by_count?: number
  abstract?: string
  abstract_missing?: boolean
  retracted?: boolean | null
  concern?: boolean | null
}

export interface Summary {
  session: string
  question: string
  pool: number
  screened: number
  remaining: number
  batches: { batch: number; size: number; rated: number; 高: number; 中: number; 低: number; 不相关: number }[]
  stop_suggested: boolean
  stop_reason: string
  api_warnings: string[]
  current_batch: BatchPaper[]
}

export interface SessionRecord {
  id: string
  question: string
  mode: string
  updated: string
  items: Item[]
}

export interface SessionListEntry {
  session: string
  question: string
  updated: string
  items: number
  selected: number
  deep: boolean
}

export interface Progress {
  event: "start" | "next"
  state: "running" | "idle" | "failed"
  started: number
  updated: number
  phase: { name: string; done: number; total: number | null; started: number } | null
  phases: { name: string; seconds: number }[]
  seconds?: number
}

interface BackendInfo {
  port: number
  token: string
}

let backend: Promise<BackendInfo> | null = null

function getBackend(): Promise<BackendInfo> {
  // In the Tauri window the shell knows where the backend is; in a plain browser
  // (vite dev without Tauri) give it as ?port=…&token=… in the address.
  backend ??= "__TAURI_INTERNALS__" in window
    ? invoke<BackendInfo>("backend")
    : Promise.resolve(Object.fromEntries(new URLSearchParams(location.search)) as unknown as BackendInfo)
  return backend
}

/** A failed backend call; ``message`` is the backend's ``detail`` (already user-facing text where it can be). */
export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

/** Text to show for a failed call: the backend's own words, or a generic sentence (docs/UI_COPY.md section 7). */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404 && e.message === "Not Found") return "后台版本与界面不一致，请重启应用"
    return e.message
  }
  return "无法连接后台，请重启应用"
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const { port, token } = await getBackend()
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-LitLoop-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      const d = JSON.parse(text).detail
      if (typeof d === "string") detail = d
    } catch { /* not JSON: keep the text */ }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  sessions: () => call<SessionListEntry[]>("/api/sessions"),
  session: (id: string) => call<{ record: SessionRecord; summary: Summary | null }>(`/api/sessions/${encodeURIComponent(id)}`),
  progress: (id: string) => call<Progress | null>(`/api/sessions/${encodeURIComponent(id)}/progress`),
  rate: (id: string, paper: { doi?: string; title: string }, relevance: Relevance | null) =>
    call<{ item: Item | null; edited: number }>(`/api/sessions/${encodeURIComponent(id)}/rating`,
      { doi: paper.doi ?? null, title: paper.title, relevance }),
  decide: (id: string, paper: { doi?: string; title: string }, decision: Item["decision"]) =>
    call<{ item: Item }>(`/api/sessions/${encodeURIComponent(id)}/decision`,
      { doi: paper.doi ?? null, title: paper.title, decision }),
  translation: () => call<TranslationInfo>("/api/translation"),
  next: (id: string) => call<{ started: boolean }>(`/api/sessions/${encodeURIComponent(id)}/next`, {}),
  translate: (id: string, paper: { doi?: string; title: string }) =>
    call<{ abstract_zh: string }>(`/api/sessions/${encodeURIComponent(id)}/translate`, { doi: paper.doi ?? null, title: paper.title }),
}

/** Who translates abstracts, which ratings without asking, and whether the app can (a key is set). */
export interface TranslationInfo {
  provider: "deepseek" | "deepl" | "agent"
  auto: Relevance[]
  ready: boolean
}

/** The rating that counts: the user's, if they changed it, else the agent's. */
export const rating = (it: Item) => it.user_relevance ?? it.relevance

/** The same key the backend uses to find a paper: DOI, or the title's letters and digits. */
export const paperKey = (p: { doi?: string; title?: string }) =>
  p.doi ? p.doi.toLowerCase() : "title:" + (p.title ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "")

// ------------------------------------------------------------------ settings

export interface SettingField {
  key: string
  label: string
  type: "secret" | "text" | "email" | "path" | "bool" | "select"
  /** For a path: what the chooser next to it picks. */
  pick?: "dir" | "file"
  value: string | boolean | null
  set: boolean
  source: "file" | "env" | "default"
  env?: string
  help?: string
  placeholder?: string
  link?: string
  warn?: boolean
  options?: [string, string][]
}

export interface SettingGroup {
  id: string
  title: string
  test?: string
  fields: SettingField[]
}

export interface TestResult {
  name: string
  ok: boolean | null // null: not configured, nothing to test
  detail: string
  cost?: string
  guides?: Guide[]
}

export interface Guide {
  title: string
  steps: string[]
  copy?: { label: string; lines: string[] }[]
  note?: string
}

export interface ProviderUsage {
  requests_today: number
  credits_today: number
  throttled_today: number
  remaining: number | null
  limit: number | null
  resets_at: string | null
  checked_at: string | null
}

export const settingsApi = {
  list: () => call<SettingGroup[]>("/api/settings"),
  save: (key: string, value: unknown) => call<SettingField>("/api/settings", { key, value }),
  onboarding: () => call<{ done: boolean; steps: { group: string; intro: string }[] }>("/api/onboarding"),
  finishOnboarding: () => call<{ done: boolean }>("/api/onboarding", { done: true }),
  test: (group: string) => call<{ results: TestResult[]; seconds: number }>(`/api/settings/test/${group}`, {}),
  updates: () => call<{ name: string; installed: string; latest: string; how: string }[]>("/api/updates"),
  quota: () => call<Record<string, ProviderUsage>>("/api/quota"),
  openalex: (fresh = false) => call<{ credits_used?: number; credits_remaining?: number; credits_limit?: number; resets_at?: string; has_key?: boolean; error?: string } | null>(`/api/quota/openalex${fresh ? "?fresh=true" : ""}`),
}

// ------------------------------------------------------------------ downloads

export interface DownloadItem {
  doi: string
  title: string
  year?: number | null
  journal?: string | null
  status: "waiting" | "done" | "failed"
  /** A lane did not get it; the next lane is trying (download_jobs). */
  retrying?: boolean
  /** Where it is now, while the job runs ("Sci-Hub 有下载链接，正在核对"). */
  stage?: string | null
  /** What the user should know about the file (a chapter delivered as its whole book). */
  note?: string | null
  source?: string | null
  file?: string | null
  error?: string | null
  /** Shown in the app (docs/UI_COPY.md section 7); the raw error goes in the tooltip. */
  error_text?: string | null
  /** The ways it was tried (quick API misses left out), see docs/APP_DATA.md. */
  attempts?: DownloadStep[] | null
  zotero?: { status: "pushing" | "added" | "reused" | "no_pdf" | "failed"; key?: string | null; error?: string | null; error_text?: string | null } | null
}

export interface DownloadStep {
  source: string
  route: "free" | "school" | null
  seconds: number
  ok: boolean
  /** Why it failed, in plain words. */
  why?: string | null
}

export type JobState = "running" | "done" | "cancelled" | "failed" | "interrupted"

export interface DownloadJobEntry {
  job: string
  title: string
  session?: string | null
  state: JobState
  created: string
  finished?: string | null
  total: number
  done: number
  failed: number
}

export interface DownloadJob extends Omit<DownloadJobEntry, "total" | "done" | "failed"> {
  output_dir: string
  error?: string | null
  /** Shown in the app (docs/UI_COPY.md section 7); the raw error goes in the tooltip. */
  error_text?: string | null
  items: DownloadItem[]
  zotero?: { state: "running" | "done" | "failed" | "interrupted"; collection: string; error?: string | null; error_text?: string | null } | null
  /** Where a push goes unless another collection is chosen. */
  zotero_default: string
}

export interface ZoteroCollection { key: string; name: string; parent: string | null; path: string }

/** The engine's process-wide report while a job runs (progress_reporter). */
export interface LiveDownload {
  total: number
  done: number
  success: number
  failed: number
  current?: string
  phase?: string
  attention?: Record<string, { message: string; current?: string }>
}

export interface PendingGroup {
  session: string
  question: string
  papers: { doi: string; title?: string; year?: number | null; journal?: string | null }[]
}

/** One screening session in the history page (screening.history). */
export interface HistoryEntry {
  session: string
  question: string
  mode: string
  created: string
  updated: string
  items: number
  by_relevance: Record<string, number>
  selected: { doi?: string; title: string; year?: number | null; journal?: string | null; journal_rank?: string | null }[]
}

export const historyApi = {
  list: () => call<HistoryEntry[]>("/api/history"),
  export: (id: string, format: "bibtex" | "ris" | "doi", which: "selected" | "high" = "selected") =>
    call<{ file: string; count: number }>(`/api/sessions/${encodeURIComponent(id)}/export`, { format, which }),
}

/** The app's search form (agent_runner.build_prompt). */
export interface SearchForm {
  question: string
  criteria: string
  mode: "quick" | "deep"
  limit: number
  batches: number
  year_from: number | null
  year_to: number | null
  max_cas_zone: number | null
  max_jcr_quartile: number | null
  exclude_warning: boolean
  seeds: string
  seed_collection: string
}

/** A search the app sent to the agent: agent_runs/<run>.json. */
export interface SearchRun {
  run: string
  state: "running" | "done" | "failed" | "cancelled" | "interrupted"
  started: string
  finished?: string
  question: string
  mode: string
  doing: string | null
  events?: { t: string; text: string }[]
  session: string | null
  result: string | null
  error: string | null
}

export const searchApi = {
  start: (form: SearchForm) => call<SearchRun>("/api/searches", form),
  list: () => call<SearchRun[]>("/api/searches"),
  get: (run: string) => call<SearchRun>(`/api/searches/${encodeURIComponent(run)}`),
  cancel: (run: string) => call<{ cancelled: boolean }>(`/api/searches/${encodeURIComponent(run)}/cancel`, {}),
}

export const downloadsApi = {
  list: () => call<{ jobs: DownloadJobEntry[]; running: string | null; live: LiveDownload | null; pushing: string | null; output_dir: string }>("/api/downloads"),
  pending: () => call<PendingGroup[]>("/api/downloads/pending"),
  job: (id: string) => call<DownloadJob>(`/api/downloads/${encodeURIComponent(id)}`),
  start: (papers: PendingGroup["papers"], title: string, session?: string, zoteroTarget?: string) =>
    call<DownloadJob>("/api/downloads", { papers, title, session: session ?? null, zotero_target: zoteroTarget ?? null }),
  cancel: () => call<{ cancelled: boolean }>("/api/downloads/cancel", {}),
  skip: (doi: string) => call<{ skipped: boolean }>("/api/downloads/skip", { doi }),
  open: (path: string) => call<{ opened: string }>("/api/open", { path }),
  push: (job: string, collection: string) => call<DownloadJob>(`/api/downloads/${encodeURIComponent(job)}/zotero`, { collection, tags: [] }),
  collections: () => call<ZoteroCollection[]>("/api/zotero/collections"),
}

// ------------------------------------------------------------------ zotero

export interface ZoteroPendingGroup {
  job: string
  title: string
  created: string
  collection: string
  papers: { doi: string; title?: string; year?: number | null; journal?: string | null; zotero?: string | null }[]
}

export interface ZoteroHistoryEntry {
  job: string
  title: string
  state: "running" | "done" | "failed" | "interrupted"
  collection: string
  started?: string
  finished?: string | null
  error?: string | null
  /** Shown in the app (docs/UI_COPY.md section 7); the raw error goes in the tooltip. */
  error_text?: string | null
  added: number
  reused: number
  no_pdf: number
  failed: number
}

export interface ZoteroOverview {
  running: boolean
  mcp: string | null
  pending: ZoteroPendingGroup[]
  history: ZoteroHistoryEntry[]
  pushing: string | null
}

/** How zotero-mcp writes: local (recommended), web, hybrid (local reads, web writes) or none. */
export type WriteMode = "local" | "web" | "hybrid" | "none"

export interface ZoteroCheckItem {
  key: string
  title: string
  doi: string
  year: number | null
  journal: string | null
  state: "ok" | "missing" | "empty"
  empty_attachments: string[]
  local_pdf: string | null
}

export const zoteroApi = {
  overview: () => call<ZoteroOverview>("/api/zotero"),
  writeMode: () => call<{ mode: WriteMode | null; local_supported?: boolean | null }>("/api/zotero/write-mode"),
  authorize: () => call<{ mode: WriteMode | null; ok: boolean; message?: string | null }>("/api/zotero/authorize", {}),
  check: (key: string) => call<{ items: ZoteroCheckItem[] }>("/api/zotero/check", { key }),
  repair: (it: ZoteroCheckItem, attach: boolean) =>
    call<{ trashed: string[]; attached: boolean }>("/api/zotero/repair",
      { item_key: it.key, doi: it.doi, trash: it.empty_attachments, attach }),
}
