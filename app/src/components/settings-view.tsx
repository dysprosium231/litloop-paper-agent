import * as React from "react"
import {
  BookMarkedIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  DatabaseIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FolderOpenIcon,
  GaugeIcon,
  GlobeIcon,
  GraduationCapIcon,
  LoaderIcon,
  SearchIcon,
  XCircleIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { SidebarContent, SidebarHeader, SidebarInput } from "@/components/ui/sidebar"
import { Switch } from "@/components/ui/switch"
import { settingsApi, type ProviderUsage, type SettingField, type SettingGroup, type TestResult, errorMessage } from "@/lib/api"
import { cn } from "@/lib/utils"

// The native chooser exists only inside the app window, not in a browser preview.
const inApp = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window

async function choosePath(pick: "dir" | "file", current: string): Promise<string | null> {
  const { open } = await import("@tauri-apps/plugin-dialog")
  const got = await open({ directory: pick === "dir", multiple: false, defaultPath: current || undefined })
  return typeof got === "string" ? got : null
}

export const QUOTA = "quota"

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  sources: DatabaseIcon, downloads: DownloadIcon, network: GlobeIcon, school: GraduationCapIcon,
  zotero: BookMarkedIcon, [QUOTA]: GaugeIcon,
}

/** Second column on the settings section: a search box and the groups. */
export function SettingsNav({ groups, current, onPick }: { groups: SettingGroup[]; current: string; onPick: (id: string) => void }) {
  const [query, setQuery] = React.useState("")
  const q = query.trim().toLowerCase()
  const entries = [...groups.map((g) => ({ id: g.id, title: g.title, words: g.fields.map((f) => f.label).join(" ") })),
    { id: QUOTA, title: "接口额度", words: "额度 用量 限流 OpenAlex Scopus" }]
  const shown = q ? entries.filter((e) => (e.title + " " + e.words).toLowerCase().includes(q)) : entries
  return (
    <>
      <SidebarHeader className="gap-3 p-4">
        <div className="relative">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <SidebarInput className="pl-8" placeholder="搜索" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className="px-2 pt-1 text-xs text-muted-foreground">设置</div>
      </SidebarHeader>
      <SidebarContent className="scrollable px-2">
        {shown.map((e) => {
          const Icon = ICONS[e.id] ?? DatabaseIcon
          return (
            <button key={e.id} type="button" onClick={() => onPick(e.id)}
              className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-sidebar-foreground/85 hover:bg-sidebar-accent",
                e.id === current && "bg-sidebar-accent font-medium text-sidebar-accent-foreground")}>
              <Icon className="size-4 shrink-0" />
              <span>{e.title}</span>
            </button>
          )
        })}
        {shown.length === 0 && <p className="px-3 py-2 text-sm text-muted-foreground">没有匹配的设置</p>}
      </SidebarContent>
    </>
  )
}

function ResultIcon({ ok }: { ok: boolean | null }) {
  if (ok === true) return <CheckCircle2Icon className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
  if (ok === false) return <XCircleIcon className="size-4 shrink-0 text-destructive" />
  return <CircleDashedIcon className="size-4 shrink-0 text-muted-foreground" />
}

const SOURCE: Record<SettingField["source"], string> = { file: "", env: "来自环境变量", default: "" }

/** A settings row: name (and a short note) on the left, the control on the right. */
function Row({ title, note, children, wide }: { title: React.ReactNode; note?: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={cn("flex flex-wrap gap-x-6 gap-y-3 border-b py-4 last:border-b-0", wide ? "flex-col" : "items-center")}>
      <div className="min-w-48 flex-1 space-y-1">
        <div className="text-[15px]">{title}</div>
        {note && <div className="text-sm text-muted-foreground">{note}</div>}
      </div>
      <div className={cn("max-w-full min-w-0", wide && "w-full")}>{children}</div>
    </div>
  )
}

/** One setting. Switches and choices save at once; text saves on Enter or the save button. */
function Field({ field, onSaved }: { field: SettingField; onSaved: (f: SettingField) => void }) {
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState(field.type === "secret" ? "" : String(field.value ?? ""))
  const [state, setState] = React.useState<"idle" | "saving" | "saved" | string>("idle")
  React.useEffect(() => { if (field.type !== "secret") setDraft(String(field.value ?? "")) }, [field])

  const save = async (value: unknown) => {
    setState("saving")
    try {
      onSaved(await settingsApi.save(field.key, value))
      setState("saved")
      setEditing(false)
      setTimeout(() => setState("idle"), 1500)
    } catch (e) {
      setState(errorMessage(e))
    }
  }
  const id = `setting-${field.key}`
  const dirty = field.type !== "secret" && draft !== String(field.value ?? "")

  let control: React.ReactNode
  let wide = false
  if (field.type === "bool") {
    control = <Switch id={id} aria-label={field.label} checked={Boolean(field.value)} onCheckedChange={(v) => save(v)} />
  } else if (field.type === "select") {
    control = (
      <Select value={String(field.value ?? "")} onValueChange={(v) => save(v)}>
        <SelectTrigger id={id} aria-label={field.label} className="w-56"><SelectValue /></SelectTrigger>
        <SelectContent>
          {field.options?.map(([v, label]) => <SelectItem key={v} value={v}>{label}</SelectItem>)}
        </SelectContent>
      </Select>
    )
  } else if (field.type === "secret" && !editing) {
    control = (
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm text-muted-foreground">{field.set ? field.value : "未填写"}</span>
        <Button variant="outline" size="sm" onClick={() => setEditing(true)}>{field.set ? "更换" : "填写"}</Button>
        {field.set && field.source === "file" && <Button variant="ghost" size="sm" onClick={() => save("")}>清除</Button>}
      </div>
    )
  } else {
    wide = field.type === "path"
    control = (
      <form className={cn("flex gap-2", wide ? "w-full" : "w-80 max-w-full")} onSubmit={(e) => { e.preventDefault(); save(draft) }}>
        <Input id={id} aria-label={field.label} type={field.type === "secret" ? "password" : field.type === "email" ? "email" : "text"}
          autoComplete="off" spellCheck={false} value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder={field.placeholder ?? (field.type === "secret" ? "粘贴新的 Key" : field.type === "email" ? "name@example.edu" : "")}
          autoFocus={field.type === "secret"} />
        {field.pick && inApp && (
          <Button type="button" variant="outline" size="icon" className="shrink-0" aria-label={field.pick === "dir" ? "选择文件夹" : "选择文件"}
            title={field.pick === "dir" ? "选择文件夹" : "选择文件"}
            onClick={async () => {
              try {
                const p = await choosePath(field.pick!, draft)
                if (p) { setDraft(p); save(p) }
              } catch (e) {
                setState("无法打开选择窗口，请重启应用")  // e.g. a window built before the dialog plugin
              }
            }}>
            <FolderOpenIcon />
          </Button>
        )}
        {(field.type === "secret" || dirty) && <Button type="submit" size="sm" className="h-9">保存</Button>}
        {field.type === "secret" && <Button type="button" variant="ghost" size="sm" className="h-9" onClick={() => setEditing(false)}>取消</Button>}
      </form>
    )
  }

  const title = (
    <span className="flex items-center gap-2">
      {field.label}
      {SOURCE[field.source] && field.set && <span className="text-xs text-muted-foreground">{SOURCE[field.source]}</span>}
      {state === "saving" && <LoaderIcon className="size-3.5 animate-spin text-muted-foreground" />}
      {state === "saved" && <span className="text-xs text-emerald-600 dark:text-emerald-400">已保存</span>}
      {field.link && (
        <a href={field.link} target="_blank" rel="noreferrer" className="flex items-center gap-0.5 text-xs text-primary hover:underline">
          申请<ExternalLinkIcon className="size-3" />
        </a>
      )}
    </span>
  )
  const error = !["idle", "saving", "saved"].includes(state) ? <span className="text-destructive">{state}</span> : null
  const note = error ?? (field.help ? <span className={cn(field.warn && "text-warning-foreground")}>{field.help}</span> : undefined)
  return <Row title={title} note={note} wide={wide}>{control}</Row>
}

/** One line to paste into another program, with a copy button. */
function CopyLine({ text }: { text: string }) {
  const [done, setDone] = React.useState(false)
  const copy = () => navigator.clipboard.writeText(text).then(() => {
    setDone(true)
    setTimeout(() => setDone(false), 1200)
  })
  return (
    <div className="flex items-center gap-2 rounded-md border bg-background px-2 py-1">
      <code className="min-w-0 flex-1 truncate font-mono text-xs">{text}</code>
      <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={copy}>{done ? "已复制" : "复制"}</Button>
    </div>
  )
}

function Page({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-3xl px-8 py-8">
      <div className="mb-2 flex items-center gap-4">
        <h1 className="text-lg font-semibold">{title}</h1>
        <div className="ml-auto">{action}</div>
      </div>
      {children}
    </div>
  )
}

// Keyed by group id: switching groups starts a fresh pane, so a check still running
// for the previous group cannot show its results here.
function GroupPane({ group, onSaved }: { group: SettingGroup; onSaved: (f: SettingField) => void }) {
  const [results, setResults] = React.useState<TestResult[] | null>(null)
  const [testing, setTesting] = React.useState(false)
  const test = async () => {
    setTesting(true)
    try {
      setResults((await settingsApi.test(group.test!)).results)
    } catch (e) {
      setResults([{ name: "检测", ok: false, detail: errorMessage(e) }])
    } finally {
      setTesting(false)
    }
  }
  const action = group.test && (
    <Button variant="outline" size="sm" onClick={test} disabled={testing}>
      {testing && <LoaderIcon className="animate-spin" />}检测
    </Button>
  )
  return (
    <Page title={group.title} action={action}>
      {results && (
        <div className="my-3 space-y-2 rounded-xl bg-muted/50 p-4">
          {results.map((r) => (
            <div key={r.name} className="space-y-2">
              <div className="flex items-start gap-2 text-sm">
                <ResultIcon ok={r.ok} />
                <span className="w-32 shrink-0">{r.name}</span>
                <span className="text-muted-foreground">{r.detail}{r.cost && <span className="ml-1 text-xs">（{r.cost}）</span>}</span>
              </div>
              {r.guides?.map((g) => (
                <section key={g.title} className="ml-6 min-w-0 space-y-3 rounded-lg border bg-background p-4">
                  <h3 className="text-sm font-medium">{g.title}</h3>
                  <ol className="list-decimal space-y-1.5 pl-5 text-sm text-foreground/90">
                    {g.steps.map((s) => <li key={s}>{s}</li>)}
                  </ol>
                  {g.copy?.filter((c) => c.lines.length).map((c) => (
                    <div key={c.label} className="space-y-1.5">
                      <div className="text-xs text-muted-foreground">{c.label}</div>
                      {c.lines.map((line) => <CopyLine key={line} text={line} />)}
                    </div>
                  ))}
                  {g.note && <p className="text-xs text-muted-foreground">{g.note}</p>}
                </section>
              ))}
            </div>
          ))}
        </div>
      )}
      <div>{group.fields.map((f) => <Field key={f.key} field={f} onSaved={onSaved} />)}</div>
    </Page>
  )
}

const PROVIDERS: Record<string, string> = {
  openalex: "OpenAlex", scopus: "Elsevier Scopus", springer: "Springer Nature", crossref: "Crossref",
  semantic_scholar: "Semantic Scholar",
}

function QuotaPane() {
  const [usage, setUsage] = React.useState<Record<string, ProviderUsage> | null>(null)
  const [live, setLive] = React.useState<Awaited<ReturnType<typeof settingsApi.openalex>>>(null)
  const load = React.useCallback(() => {
    settingsApi.quota().then(setUsage).catch(() => {})
    settingsApi.openalex().then(setLive).catch(() => {})
  }, [])
  React.useEffect(load, [load])
  const pct = live?.credits_limit ? (live.credits_remaining ?? 0) / live.credits_limit : null
  return (
    <Page title="接口额度" action={<Button variant="outline" size="sm" onClick={load}>刷新</Button>}>
      {live && !live.error && (
        <Row title="OpenAlex 今日剩余" note={`重置 ${live.resets_at ? new Date(live.resets_at).toLocaleString() : "未知"}`}>
          <div className="w-56 space-y-1.5 text-right">
            <div className="text-sm tabular-nums">{live.credits_remaining?.toLocaleString()} / {live.credits_limit?.toLocaleString()} 积分</div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className={cn("h-full rounded-full", pct !== null && pct < 0.2 ? "bg-amber-500" : "bg-primary")} style={{ width: `${(pct ?? 0) * 100}%` }} />
            </div>
          </div>
        </Row>
      )}
      {usage && Object.entries(usage).map(([name, u]) => (
        <Row key={name} title={PROVIDERS[name] ?? name}
          note={`今日请求 ${u.requests_today} 次` + (u.throttled_today ? ` · 被限流 ${u.throttled_today} 次` : "") + (u.resets_at ? ` · 重置 ${u.resets_at}` : "")}>
          <span className="text-sm tabular-nums text-muted-foreground">
            {u.remaining == null ? "—" : (u.limit != null ? `剩余 ${u.remaining.toLocaleString()} / ${u.limit.toLocaleString()}` : `剩余 ${u.remaining.toLocaleString()}`)}
          </span>
        </Row>
      ))}
    </Page>
  )
}

/** Main area on the settings section. */
export function SettingsPane({ groups, current, onSaved }: { groups: SettingGroup[]; current: string; onSaved: (f: SettingField) => void }) {
  if (current === QUOTA) return <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto scrollable"><QuotaPane /></div>
  const group = groups.find((g) => g.id === current)
  if (!group) return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">正在读取设置…</div>
  return <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto scrollable"><GroupPane key={group.id} group={group} onSaved={onSaved} /></div>
}
