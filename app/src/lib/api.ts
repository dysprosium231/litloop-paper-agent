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

async function call<T>(path: string, body?: unknown): Promise<T> {
  const { port, token } = await getBackend()
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-LitLoop-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

export const api = {
  sessions: () => call<SessionListEntry[]>("/api/sessions"),
  session: (id: string) => call<{ record: SessionRecord; summary: Summary | null }>(`/api/sessions/${encodeURIComponent(id)}`),
  progress: (id: string) => call<Progress | null>(`/api/sessions/${encodeURIComponent(id)}/progress`),
  rate: (id: string, paper: { doi?: string; title: string }, relevance: Relevance | null) =>
    call<{ item: Item; edited: number }>(`/api/sessions/${encodeURIComponent(id)}/rating`,
      { doi: paper.doi ?? null, title: paper.title, relevance }),
  decide: (id: string, paper: { doi?: string; title: string }, decision: Item["decision"]) =>
    call<{ item: Item }>(`/api/sessions/${encodeURIComponent(id)}/decision`,
      { doi: paper.doi ?? null, title: paper.title, decision }),
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
  test: (group: string) => call<{ results: TestResult[]; seconds: number }>(`/api/settings/test/${group}`, {}),
  quota: () => call<Record<string, ProviderUsage>>("/api/quota"),
  openalex: () => call<{ credits_used?: number; credits_remaining?: number; credits_limit?: number; resets_at?: string; has_key?: boolean; error?: string } | null>("/api/quota/openalex"),
}

// ------------------------------------------------------------------ downloads

export interface DownloadItem {
  doi: string
  title: string
  year?: number | null
  journal?: string | null
  status: "waiting" | "done" | "failed"
  source?: string | null
  file?: string | null
  error?: string | null
  zotero?: { status: "pushing" | "added" | "reused" | "no_pdf" | "failed"; key?: string | null; error?: string | null } | null
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
  items: DownloadItem[]
  zotero?: { state: "running" | "done" | "failed" | "interrupted"; collection: string; error?: string | null } | null
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

export const downloadsApi = {
  list: () => call<{ jobs: DownloadJobEntry[]; running: string | null; live: LiveDownload | null; pushing: string | null }>("/api/downloads"),
  pending: () => call<PendingGroup[]>("/api/downloads/pending"),
  job: (id: string) => call<DownloadJob>(`/api/downloads/${encodeURIComponent(id)}`),
  start: (papers: PendingGroup["papers"], title: string, session?: string) =>
    call<DownloadJob>("/api/downloads", { papers, title, session: session ?? null }),
  cancel: () => call<{ cancelled: boolean }>("/api/downloads/cancel", {}),
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

export const zoteroApi = {
  overview: () => call<ZoteroOverview>("/api/zotero"),
}
