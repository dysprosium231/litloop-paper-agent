import * as React from "react"
import {
  BookMarkedIcon,
  BotIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  DatabaseIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FolderOpenIcon,
  GaugeIcon,
  GlobeIcon,
  GraduationCapIcon,
  PuzzleIcon,
  LanguagesIcon,
  LoaderIcon,
  SearchIcon,
  XCircleIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, PageBody, PageHeader } from "@/components/page"
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
  zotero: BookMarkedIcon, screening: LanguagesIcon, [QUOTA]: GaugeIcon, agent: BotIcon, components: PuzzleIcon,
}

// Groups of the settings list; ids the backend adds later fall into the last one.
const SECTIONS: { title: string; ids: string[] }[] = [
  { title: "检索与筛选", ids: ["sources", "screening", QUOTA] },
  { title: "下载与连接", ids: ["downloads", "network", "school", "zotero"] },
]

// One line under each page title.
const ABOUT: Record<string, string> = {
  sources: "检索接口的 API Key 和联系邮箱。",
  downloads: "PDF 的保存位置、文件名和下载方式。",
  network: "代理和校园网的访问方式。",
  school: "通过学校认证下载订阅期刊。",
  zotero: "推送到 Zotero 的方式和默认分类。",
  screening: "摘要翻译的范围和翻译服务。",
  [QUOTA]: "各检索接口今日的用量和剩余额度。",
  components: "浏览器、WebVPN、代理等可选组件是否已安装。",
}

/** The settings list: a search box and the groups, inside the page (left of the content). */
function SettingsNav({ groups, current, onPick }: { groups: SettingGroup[]; current: string; onPick: (id: string) => void }) {
  const [query, setQuery] = React.useState("")
  const q = query.trim().toLowerCase()
  const entries = [...groups.map((g) => ({ id: g.id, title: g.title, words: g.fields.map((f) => f.label).join(" ") })),
    { id: QUOTA, title: "接口额度", words: "额度 用量 限流 OpenAlex Scopus" }]
  const shown = q ? entries.filter((e) => (e.title + " " + e.words).toLowerCase().includes(q)) : entries
  const known = new Set(SECTIONS.flatMap((s) => s.ids))
  const sections = SECTIONS.map((s, i) => ({
    title: s.title,
    items: shown.filter((e) => s.ids.includes(e.id) || (i === SECTIONS.length - 1 && !known.has(e.id))),
  })).filter((s) => s.items.length)
  return (
    <nav className="scrollable flex w-60 shrink-0 flex-col gap-5 overflow-y-auto border-r bg-background px-4 py-5">
      <div className="relative">
        <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="h-9 pl-8" placeholder="搜索设置" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {sections.map((s) => (
        <div key={s.title} className="space-y-1">
          <div className="px-3 pb-1 text-xs font-semibold text-muted-foreground">{s.title}</div>
          {s.items.map((e) => {
            const Icon = ICONS[e.id] ?? DatabaseIcon
            return (
              <button key={e.id} type="button" onClick={() => onPick(e.id)}
                className={cn("flex h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-accent",
                  e.id === current && "bg-primary/10 font-medium text-primary hover:bg-primary/10")}>
                <Icon className="size-[18px] shrink-0" />
                <span>{e.title}</span>
              </button>
            )
          })}
        </div>
      ))}
      {shown.length === 0 && <p className="px-3 text-sm text-muted-foreground">没有匹配的设置</p>}
    </nav>
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
    <div className={cn("flex flex-wrap gap-x-6 gap-y-3 border-b px-5 py-4 last:border-b-0", wide ? "flex-col" : "items-center")}>
      <div className="min-w-48 flex-1 space-y-1">
        <div className="text-sm font-medium">{title}</div>
        {note && <div className="text-[13px] leading-relaxed text-muted-foreground">{note}</div>}
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
                setState("无法打开选择窗口。重启 LitLoop 后重试")  // e.g. a window built before the dialog plugin
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
          申请 Key<ExternalLinkIcon className="size-3" />
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

function Page({ id, title, intro, action, children }: {
  id: string; title: string; intro?: string; action?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <PageBody>
      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-xl font-semibold">{title}</h2>
          {(intro || ABOUT[id]) && <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">{intro || ABOUT[id]}</p>}
        </div>
        {action}
      </div>
      {children}
    </PageBody>
  )
}

// Keyed by group id: switching groups starts a fresh pane, so a check still running
// for the previous group cannot show its results here.
/** One settings group: its fields, and its check when it has one. The first-run guide shows these too. */
export function GroupPane({ group, intro, onSaved }: { group: SettingGroup; intro?: string; onSaved: (f: SettingField) => void }) {
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
  // A group with nothing to set is only its check: run it on opening.
  React.useEffect(() => { if (group.test && group.fields.length === 0) test() }, [group.id])
  const action = group.test && (
    <Button variant="outline" size="sm" onClick={test} disabled={testing}>
      {testing && <LoaderIcon className="animate-spin" />}检测
    </Button>
  )
  return (
    <Page id={group.id} title={group.title} intro={intro} action={action}>
      {results && (
        <Card className="space-y-2 p-5">
          {results.map((r) => (
            <div key={r.name} className="space-y-2">
              <div className="flex items-start gap-2 text-sm">
                <ResultIcon ok={r.ok} />
                <span className="w-32 shrink-0">{r.name}</span>
                <span className="text-muted-foreground">{r.detail}{r.cost && <span className="ml-1 text-xs">（{r.cost}）</span>}</span>
              </div>
              {r.guides?.map((g) => (
                <section key={g.title} className="ml-6 min-w-0 space-y-3 rounded-lg border bg-canvas p-4">
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
        </Card>
      )}
      {group.fields.length > 0 && <Card>{group.fields.map((f) => <Field key={f.key} field={f} onSaved={onSaved} />)}</Card>}
    </Page>
  )
}

const PROVIDERS: Record<string, string> = {
  openalex: "OpenAlex", scopus: "Elsevier Scopus", springer: "Springer Nature", crossref: "Crossref",
  semantic_scholar: "Semantic Scholar",
}

function QuotaPane() {
  const [usage, setUsage] = React.useState<Record<string, ProviderUsage> | null>(null)
  const [live, setLive] = React.useState<Awaited<ReturnType<typeof settingsApi.openalex>> | undefined>(undefined)
  const load = React.useCallback((fresh: boolean) => {
    settingsApi.quota().then(setUsage).catch(() => {})
    if (fresh) setLive(undefined)
    settingsApi.openalex(fresh).then(setLive).catch(() => setLive(null))
  }, [])
  React.useEffect(() => load(false), [load])
  const pct = live?.credits_limit ? (live.credits_remaining ?? 0) / live.credits_limit : null
  return (
    <Page id={QUOTA} title="接口额度" action={<Button variant="outline" size="sm" onClick={() => load(true)}>刷新</Button>}>
      <Card>
      {live === undefined && (
        <Row title="OpenAlex 今日剩余" note="正在向 OpenAlex 查询…">
          <LoaderIcon className="size-4 animate-spin text-muted-foreground" />
        </Row>
      )}
      {live && !live.error && (
        <Row title="OpenAlex 今日剩余" note={live.resets_at ? `重置 ${new Date(live.resets_at).toLocaleString()}` : undefined}>
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
      </Card>
    </Page>
  )
}

/** The settings section: header, the list of groups, and the chosen group. */
export function SettingsPage({ groups, current, onPick, onSaved, onGuide }: {
  groups: SettingGroup[]; current: string; onPick: (id: string) => void; onSaved: (f: SettingField) => void
  onGuide: () => void
}) {
  const group = groups.find((g) => g.id === current)
  // The weekly version check (updates.py); nothing is installed from here.
  const [updates, setUpdates] = React.useState<Awaited<ReturnType<typeof settingsApi.updates>>>([])
  React.useEffect(() => { settingsApi.updates().then(setUpdates).catch(() => {}) }, [])
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="设置">
        <Button variant="outline" size="sm" onClick={onGuide}>重新运行引导</Button>
      </PageHeader>
      {updates.length > 0 && (
        <div className="space-y-0.5 border-b bg-primary/5 px-8 py-2.5 text-sm">
          {updates.map((u) => (
            <p key={u.name}>
              <span className="font-medium">{u.name} {u.latest}</span>
              <span className="text-muted-foreground"> 可更新（当前 {u.installed}）：{u.how}</span>
            </p>
          ))}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <SettingsNav groups={groups} current={current} onPick={onPick} />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {current === QUOTA ? <QuotaPane />
            : group ? <GroupPane key={group.id} group={group} onSaved={onSaved} />
            : <div className="flex flex-1 items-center justify-center bg-canvas text-sm text-muted-foreground">正在读取设置…</div>}
        </div>
      </div>
    </div>
  )
}
