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
import { downloadsApi, settingsApi, type AcademicRouteSite, type AppUpdateInfo, type ComponentStatus, type HarnessInfo, type ProviderPreset, type ProviderUsage, type RouteBenchmarkResponse, type SettingField, type SettingGroup, type TestResult, errorMessage } from "@/lib/api"
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
  zotero: BookMarkedIcon, screening: LanguagesIcon, [QUOTA]: GaugeIcon, agent: BotIcon, builtin_agent: BotIcon, components: PuzzleIcon,
}

// Groups of the settings list; ids the backend adds later fall into the last one.
const SECTIONS: { title: string; ids: string[] }[] = [
  { title: "检索与筛选", ids: ["builtin_agent", "sources", "screening", QUOTA] },
  { title: "下载与连接", ids: ["downloads", "network", "school", "zotero", "components", "agent"] },
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
  agent: "选择在 app 中发起检索时使用的 Agent（支持 Claude Code、WorkBuddy、DeepSeek Harness、Codex CLI 或内置 Agent）。",
  builtin_agent: "不装外部 Agent 命令，让 app 直接调用模型接口完成检索与评分。",
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
    <nav className="scrollable flex flex-1 flex-col gap-5 overflow-y-auto px-4 py-5">
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

function HarnessCards({ harnesses, onReload, onFieldUpdated }: {
  harnesses: HarnessInfo[] | null
  onReload: (next?: HarnessInfo[]) => void
  onFieldUpdated: (f: SettingField) => void
}) {
  const [busy, setBusy] = React.useState<string | null>(null)
  const [feedback, setFeedback] = React.useState<{ id: string; ok: boolean; text: string } | null>(null)

  if (!harnesses) {
    return (
      <Card className="flex items-center gap-2 p-5 text-sm text-muted-foreground">
        <LoaderIcon className="size-4 animate-spin" />正在检测已安装的 Agent…
      </Card>
    )
  }

  const handleSelect = async (id: string) => {
    setBusy(`select:${id}`)
    setFeedback(null)
    try {
      const next = await settingsApi.selectHarness(id)
      onReload(next)
      const gs = await settingsApi.list()
      const ag = gs.find((g) => g.id === "agent")
      ag?.fields.forEach(onFieldUpdated)
    } catch (e) {
      setFeedback({ id, ok: false, text: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  const handleSync = async (id: string) => {
    setBusy(`sync:${id}`)
    setFeedback(null)
    try {
      const res = await settingsApi.syncHarness(id)
      onReload(harnesses.map((h) => (h.id === id ? res.harness : h)))
      setFeedback({ id, ok: res.ok, text: res.message || "已同步 MCP 与技能配置" })
    } catch (e) {
      setFeedback({ id, ok: false, text: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card className="divide-y">
      {harnesses.map((h) => {
        const synced = h.mcp_configured && h.skill_installed
        const selecting = busy === `select:${h.id}`
        const syncing = busy === `sync:${h.id}`
        return (
          <div key={h.id} className="space-y-2 px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="min-w-48 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{h.label}</span>
                  <span className={cn(
                    "rounded px-1.5 py-0.5 text-xs",
                    h.detected
                      ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                      : "bg-muted text-muted-foreground",
                  )}>
                    {h.detected ? "已安装" : "未检测到"}
                  </span>
                  {h.can_sync && (
                    <span className={cn(
                      "rounded px-1.5 py-0.5 text-xs",
                      synced
                        ? "bg-primary/10 text-primary"
                        : "bg-muted text-muted-foreground",
                    )}>
                      {synced ? "MCP 与技能已就绪" : "未同步全局配置"}
                    </span>
                  )}
                </div>
                {h.detail && <div className="text-[13px] leading-relaxed text-muted-foreground">{h.detail}</div>}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {h.can_sync && h.detected && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={Boolean(busy)}
                    onClick={() => handleSync(h.id)}
                  >
                    {syncing && <LoaderIcon className="animate-spin" />}
                    {synced ? "重新同步配置" : "一键配置 MCP 与技能"}
                  </Button>
                )}
                <Button
                  variant={h.selected ? "secondary" : "default"}
                  size="sm"
                  disabled={h.selected || Boolean(busy)}
                  onClick={() => handleSelect(h.id)}
                >
                  {selecting && <LoaderIcon className="animate-spin" />}
                  {h.selected ? "当前使用" : "选用"}
                </Button>
              </div>
            </div>
            {feedback && feedback.id === h.id && (
              <p className={cn("text-xs", feedback.ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive")}>
                {feedback.text}
              </p>
            )}
          </div>
        )
      })}
    </Card>
  )
}

function RouteBenchmarkCard({
  fields,
  onFieldUpdated,
}: {
  fields: SettingField[]
  onFieldUpdated?: (field: SettingField) => void
}) {
  const [running, setRunning] = React.useState(false)
  const [applying, setApplying] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [appliedMsg, setAppliedMsg] = React.useState<string | null>(null)
  const [report, setReport] = React.useState<RouteBenchmarkResponse | null>(null)
  const [selectedHosts, setSelectedHosts] = React.useState<string[]>([])

  const proxyField = fields.find((f) => f.key === "host_proxy_url")
  const campusField = fields.find((f) => f.key === "is_campus_network")
  const configuredProxy = String(proxyField?.value ?? "")
  const isCampus = Boolean(campusField?.value)

  async function runBenchmark() {
    setRunning(true)
    setError(null)
    setAppliedMsg(null)
    try {
      const res = await settingsApi.benchmarkRoutes(configuredProxy, isCampus)
      setReport(res)
      setSelectedHosts(res.recommended_hosts)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setRunning(false)
    }
  }

  function toggleHost(host: string) {
    setSelectedHosts((prev) => (prev.includes(host) ? prev.filter((h) => h !== host) : [...prev, host]))
    setAppliedMsg(null)
  }

  async function applySelection() {
    if (!report) return
    setApplying(true)
    setError(null)
    setAppliedMsg(null)
    try {
      const res = await settingsApi.applyHostProxy(report.proxy_url, selectedHosts)
      if (proxyField && res.proxy_url && res.proxy_url !== configuredProxy) {
        onFieldUpdated?.({ ...proxyField, value: res.proxy_url })
      }
      setAppliedMsg(
        res.hosts.length > 0
          ? `已保存 ${res.hosts.length} 个代理域名${res.proxy_url ? `（代理 ${res.proxy_url}）` : ""}`
          : "已清空代理域名清单（全部直连）"
      )
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setApplying(false)
    }
  }

  return (
    <Card className="p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="text-sm font-medium text-foreground">学术站点分流测速</div>
          <p className="text-xs text-muted-foreground">
            同时测试直连与本地代理对常见出版商的连通性与延迟；校园网下直连可用的订阅库会保留直连，避免丢失机构 IP 权限。
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={runBenchmark} disabled={running}>
          {running && <LoaderIcon className="h-3.5 w-3.5 animate-spin mr-1.5" />}
          {running ? "测速中..." : report ? "重新测速" : "开始测速"}
        </Button>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      {report && (
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{report.proxy_url ? `代理地址：${report.proxy_url}` : "未检测到本地代理"}</span>
            <span>·</span>
            <span>{report.is_campus ? "当前为校园网模式" : "当前为非校园网模式"}</span>
          </div>

          <div className="max-h-72 overflow-y-auto rounded-md border divide-y text-xs">
            {report.sites.map((site: AcademicRouteSite) => {
              const checked = selectedHosts.includes(site.host)
              return (
                <label
                  key={site.host}
                  className="flex items-center justify-between gap-3 px-3 py-2 hover:bg-muted/40 cursor-pointer"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleHost(site.host)}
                      disabled={!report.proxy_url}
                      className="h-3.5 w-3.5 rounded border-input"
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 font-medium text-foreground">
                        <span>{site.name}</span>
                        <span className="text-[11px] font-normal text-muted-foreground truncate">{site.host}</span>
                      </div>
                      <div className="text-[11px] text-muted-foreground truncate">{site.reason}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0 tabular-nums text-[11px]">
                    <span className={site.direct.ok ? "text-foreground" : "text-destructive"}>
                      直连 {site.direct.ok ? `${site.direct.ms} ms` : `失败 (${site.direct.error || site.direct.status})`}
                    </span>
                    <span className={site.proxy ? (site.proxy.ok ? "text-foreground" : "text-destructive") : "text-muted-foreground"}>
                      代理 {site.proxy ? (site.proxy.ok ? `${site.proxy.ms} ms` : `失败 (${site.proxy.error || site.proxy.status})`) : "—"}
                    </span>
                    <span
                      className={cn(
                        "rounded px-1.5 py-0.5 font-medium",
                        site.recommendation === "proxy"
                          ? "bg-primary/10 text-primary"
                          : site.recommendation === "direct"
                            ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                            : "bg-destructive/10 text-destructive"
                      )}
                    >
                      {site.recommendation === "proxy" ? "建议代理" : site.recommendation === "direct" ? "建议直连" : "均不可达"}
                    </span>
                  </div>
                </label>
              )
            })}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
            <span className="text-xs text-muted-foreground">
              已勾选 {selectedHosts.length} 个域名走代理
              {appliedMsg ? ` · ${appliedMsg}` : ""}
            </span>
            <Button size="sm" onClick={applySelection} disabled={applying}>
              {applying && <LoaderIcon className="h-3.5 w-3.5 animate-spin mr-1.5" />}
              应用到分流清单
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}

function BuiltinPresetCard({ fields, onFieldUpdated }: {
  fields: SettingField[]
  onFieldUpdated: (f: SettingField) => void
}) {
  const [presets, setPresets] = React.useState<ProviderPreset[] | null>(null)
  const [busyId, setBusyId] = React.useState<string | null>(null)
  const [feedback, setFeedback] = React.useState<{ ok: boolean; text: string; link?: string; note?: string } | null>(null)

  React.useEffect(() => {
    let cancelled = false
    settingsApi.builtinPresets().then((list) => {
      if (!cancelled) setPresets(list)
    }).catch(() => {
      if (!cancelled) setPresets([])
    })
    return () => { cancelled = true }
  }, [])

  const currentBaseUrl = String(fields.find((f) => f.key === "builtin_agent_base_url")?.value ?? "")
  const currentProtocol = String(fields.find((f) => f.key === "builtin_agent_protocol")?.value ?? "")

  const applyPreset = async (p: ProviderPreset) => {
    setBusyId(p.id)
    setFeedback(null)
    try {
      const updated = await settingsApi.applyBuiltinPreset(p.id)
      updated.forEach(onFieldUpdated)
      setFeedback({
        ok: true,
        text: p.needs_key
          ? `已填入 ${p.label} 接口与推荐模型，填写 Key 后点右上角「检测」即可`
          : `已填入 ${p.label} 接口与推荐模型，点右上角「检测」即可`,
        link: p.link || undefined,
        note: p.note || undefined,
      })
    } catch (e) {
      setFeedback({ ok: false, text: errorMessage(e) })
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card className="space-y-3 p-5">
      <div className="space-y-1">
        <div className="text-sm font-medium">常见服务商一键预设</div>
        <p className="text-xs text-muted-foreground">点击服务商自动填入接口地址、协议与推荐模型；除本地 Ollama 外均需填写对应 Key。</p>
      </div>
      {presets === null ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <LoaderIcon className="size-3.5 animate-spin" />正在加载服务商预设…
        </div>
      ) : presets.length === 0 ? (
        <p className="text-xs text-muted-foreground">暂无可用预设</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {presets.map((p) => {
            const active = currentBaseUrl === p.base_url && currentProtocol === p.protocol
            const applying = busyId === p.id
            return (
              <Button
                key={p.id}
                type="button"
                variant={active ? "secondary" : "outline"}
                size="sm"
                disabled={Boolean(busyId)}
                onClick={() => applyPreset(p)}
                title={p.note || p.base_url}
              >
                {applying && <LoaderIcon className="animate-spin" />}
                <span>{p.label}</span>
                {!p.needs_key && <span className="text-[11px] text-muted-foreground">（免 Key）</span>}
              </Button>
            )
          })}
        </div>
      )}
      {feedback && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className={feedback.ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}>
            {feedback.text}
          </span>
          {feedback.note && <span className="text-muted-foreground">{feedback.note}</span>}
          {feedback.link && (
            <a
              href={feedback.link}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 text-primary hover:underline"
            >
              申请 Key<ExternalLinkIcon className="size-3" />
            </a>
          )}
        </div>
      )}
    </Card>
  )
}

// Keyed by group id: switching groups starts a fresh pane, so a check still running
// for the previous group cannot show its results here.
/** One settings group: its fields, and its check when it has one. The first-run guide shows these too. */
export function GroupPane({ group, intro, onSaved }: { group: SettingGroup; intro?: string; onSaved: (f: SettingField) => void }) {
  const [results, setResults] = React.useState<TestResult[] | null>(null)
  const [testing, setTesting] = React.useState(false)
  const [installing, setInstalling] = React.useState<string | null>(null)
  const [installStatus, setInstallStatus] = React.useState<{ key: string; status: ComponentStatus } | null>(null)
  const [installLog, setInstallLog] = React.useState<{ key: string; text: string } | null>(null)
  const [harnesses, setHarnesses] = React.useState<HarnessInfo[] | null>(null)
  const [showCustomCli, setShowCustomCli] = React.useState(false)

  const loadHarnesses = React.useCallback((next?: HarnessInfo[]) => {
    if (next) {
      setHarnesses(next)
      return
    }
    settingsApi.harnesses().then(setHarnesses).catch(() => {})
  }, [])

  React.useEffect(() => {
    if (group.id === "agent") loadHarnesses()
  }, [group.id, loadHarnesses])

  const handleFieldSaved = React.useCallback((f: SettingField) => {
    onSaved(f)
    if (group.id === "agent") loadHarnesses()
  }, [group.id, loadHarnesses, onSaved])
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
  React.useEffect(() => {
    if (!installing) return
    let cancelled = false
    const poll = () => {
      settingsApi.componentStatus(installing).then((status) => {
        if (!cancelled) setInstallStatus({ key: installing, status })
      }).catch(() => {})
    }
    poll()
    const timer = window.setInterval(poll, 600)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [installing])
  // Installing an optional component runs uv in the backend; when it works the
  // check runs again so the row turns green, otherwise its log is shown.
  const install = async (key: string) => {
    setInstalling(key)
    setInstallStatus(null)
    setInstallLog(null)
    try {
      const out = await settingsApi.installComponent(key)
      if (out.ok) {
        setInstallStatus(null)
        await test()
      } else {
        setInstallLog({ key, text: out.log || "安装失败" })
      }
    } catch (e) {
      setInstallLog({ key, text: errorMessage(e) })
    } finally {
      setInstalling(null)
    }
  }
  const mirrorField = group.fields.find((f) => f.key === "package_index_url")
  const tunaMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
  const retryWithTunaMirror = async (key: string) => {
    try {
      onSaved(await settingsApi.save("package_index_url", tunaMirror))
    } catch (e) {
      setInstallLog({ key, text: errorMessage(e) })
      return
    }
    await install(key)
  }
  // A group with nothing to set is only its check: run it on opening (so is a
  // group that asks for the check to run by itself, like 可选组件).
  React.useEffect(() => { if (group.test && (group.fields.length === 0 || group.auto)) test() }, [group.id])
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
                <span className="min-w-0 flex-1 text-muted-foreground">{r.detail}{r.cost && <span className="ml-1 text-xs">（{r.cost}）</span>}</span>
                {r.action && (
                  <Button variant="outline" size="sm" disabled={installing === r.action.key} onClick={() => install(r.action!.key)}>
                    {installing === r.action.key && <LoaderIcon className="animate-spin" />}{r.action.label}
                  </Button>
                )}
              </div>
              {installing === r.action?.key && (
                <div className="ml-6 space-y-1 rounded-md border bg-muted/40 px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-foreground">
                      {installStatus?.key === r.action.key && installStatus.status.stage
                        ? installStatus.status.stage
                        : "正在准备安装…"}
                    </span>
                    {installStatus?.key === r.action.key && installStatus.status.elapsed != null && (
                      <span className="tabular-nums text-muted-foreground">
                        已用时 {Math.max(0, Math.round(installStatus.status.elapsed))} 秒
                      </span>
                    )}
                  </div>
                  {installStatus?.key === r.action.key && installStatus.status.line && (
                    <div className="truncate font-mono text-muted-foreground" title={installStatus.status.line}>
                      {installStatus.status.line}
                    </div>
                  )}
                </div>
              )}
              {installLog && r.action && installLog.key === r.action.key && installing !== r.action.key && (
                <div className="ml-6 space-y-2">
                  <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap text-destructive">{installLog.text}</pre>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span>
                      {mirrorField
                        ? "安装失败，可在下方「组件下载镜像」更换镜像后重试。"
                        : "安装失败，检查网络后重试。"}
                    </span>
                    <Button variant="outline" size="sm" className="h-7 px-2.5 text-xs" onClick={() => install(r.action!.key)}>
                      重试
                    </Button>
                    {mirrorField && mirrorField.value !== tunaMirror && (
                      <Button variant="outline" size="sm" className="h-7 px-2.5 text-xs" onClick={() => retryWithTunaMirror(r.action!.key)}>
                        切换清华镜像并重试
                      </Button>
                    )}
                  </div>
                </div>
              )}
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
      {group.id === "agent" && (
        <HarnessCards harnesses={harnesses} onReload={loadHarnesses} onFieldUpdated={onSaved} />
      )}
      {group.id === "builtin_agent" && (
        <BuiltinPresetCard fields={group.fields} onFieldUpdated={handleFieldSaved} />
      )}
      {group.id === "network" && (
        <RouteBenchmarkCard fields={group.fields} onFieldUpdated={handleFieldSaved} />
      )}
      {group.fields.length > 0 && (() => {
        const harnessVal = String(group.fields.find((f) => f.key === "agent_harness")?.value ?? "")
        const isCustom = harnessVal === "custom" || harnesses?.find((h) => h.selected)?.id === "custom"
        const isAdvCli = (f: SettingField) => group.id === "agent" && (f.key === "agent_command" || f.key === "agent_args")
        const mainFields = group.id === "agent" && !isCustom ? group.fields.filter((f) => !isAdvCli(f)) : group.fields
        const advFields = group.id === "agent" && !isCustom ? group.fields.filter(isAdvCli) : []
        return (
          <Card>
            {mainFields.map((f) => <Field key={f.key} field={f} onSaved={handleFieldSaved} />)}
            {advFields.length > 0 && (
              <div className="border-t">
                <button
                  type="button"
                  onClick={() => setShowCustomCli((v) => !v)}
                  className="flex w-full items-center justify-between px-5 py-3 text-left text-xs font-medium text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                >
                  <span>高级：自定义命令与参数</span>
                  <span>{showCustomCli ? "收起" : "展开"}</span>
                </button>
                {showCustomCli && (
                  <div className="border-t">
                    {advFields.map((f) => <Field key={f.key} field={f} onSaved={handleFieldSaved} />)}
                  </div>
                )}
              </div>
            )}
          </Card>
        )
      })()}
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
  const [diag, setDiag] = React.useState<{ path: string; size: number } | null>(null)
  const [diagBusy, setDiagBusy] = React.useState(false)
  const [diagError, setDiagError] = React.useState<string | null>(null)
  // Where the papers and sessions live; the installer never touches this folder.
  const [dataDir, setDataDir] = React.useState<string | null>(null)
  React.useEffect(() => { settingsApi.dataDir().then((r) => setDataDir(r.path)).catch(() => {}) }, [])
  const [appUpdate, setAppUpdate] = React.useState<AppUpdateInfo | null>(null)
  const [checkingUpdate, setCheckingUpdate] = React.useState(false)
  const [updateCheckedFresh, setUpdateCheckedFresh] = React.useState(false)
  const [updateFetchError, setUpdateFetchError] = React.useState<string | null>(null)
  const [installingUpdate, setInstallingUpdate] = React.useState(false)
  const [installStage, setInstallStage] = React.useState<string | null>(null)
  const [installError, setInstallError] = React.useState<string | null>(null)
  React.useEffect(() => {
    settingsApi.checkAppUpdate(false).then(setAppUpdate).catch(() => {})
  }, [])
  const checkAppUpdate = async () => {
    setCheckingUpdate(true)
    setUpdateFetchError(null)
    setInstallError(null)
    try {
      const info = await settingsApi.checkAppUpdate(true)
      setAppUpdate(info)
      setUpdateCheckedFresh(true)
    } catch (e) {
      setUpdateFetchError(errorMessage(e))
    } finally {
      setCheckingUpdate(false)
    }
  }
  const installAppUpdate = async () => {
    setInstallingUpdate(true)
    setInstallError(null)
    setInstallStage("正在检查自动更新包…")
    try {
      const { check } = await import("@tauri-apps/plugin-updater")
      const { relaunch } = await import("@tauri-apps/plugin-process")
      const update = await check()
      if (!update) {
        setInstallError("当前发布未检测到签名更新包，请点击下方按钮前往下载")
        return
      }
      let total = 0
      let downloaded = 0
      await update.downloadAndInstall((ev) => {
        if (ev.event === "Started") {
          total = ev.data.contentLength ?? 0
          setInstallStage("正在下载更新…")
        } else if (ev.event === "Progress") {
          downloaded += ev.data.chunkLength
          setInstallStage(
            total > 0
              ? `正在下载更新 ${Math.min(100, Math.round((downloaded / total) * 100))}%`
              : "正在下载更新…"
          )
        } else if (ev.event === "Finished") {
          setInstallStage("下载完成，正在安装并重启…")
        }
      })
      await relaunch()
    } catch (e) {
      setInstallError(errorMessage(e))
    } finally {
      setInstallingUpdate(false)
      setInstallStage(null)
    }
  }
  const formatVersion = (v: string) => (v.startsWith("v") || v.startsWith("V") ? v.slice(1) : v)
  const exportDiagnostics = () => {
    setDiagError(null)
    setDiagBusy(true)
    settingsApi.diagnostics().then(setDiag).catch((e) => setDiagError(errorMessage(e))).finally(() => setDiagBusy(false))
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="设置">
        {diag && (
          <Button variant="outline" size="sm" onClick={() => downloadsApi.open(diag.path).catch(() => {})} title={diag.path}>
            打开文件夹
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={exportDiagnostics} disabled={diagBusy}>
          {diagBusy ? "正在导出…" : "导出诊断包"}
        </Button>
        <Button variant="outline" size="sm" onClick={onGuide}>重新运行引导</Button>
      </PageHeader>
      {dataDir && (
        <div className="flex flex-wrap items-center gap-x-2 border-b bg-muted/40 px-8 py-2 text-sm text-muted-foreground">
          <span>数据目录</span>
          <span className="font-mono text-xs text-foreground" title={dataDir}>{dataDir}</span>
          <span>（论文、检索记录与设置都在这里，卸载不会删除）</span>
          <Button variant="link" size="sm" className="h-auto px-0" onClick={() => downloadsApi.open(dataDir).catch(() => {})}>
            打开
          </Button>
        </div>
      )}
      {(diag || diagError) && (
        <div className="space-y-0.5 border-b bg-primary/5 px-8 py-2.5 text-sm">
          {diag && (
            <p>
              <span className="font-medium">诊断包已保存</span>
              <span className="text-muted-foreground">：{diag.path}（{(diag.size / 1024).toFixed(0)} KB，设置里的密钥已打码）</span>
            </p>
          )}
          {diagError && <p className="text-destructive">{diagError}</p>}
        </div>
      )}
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
        <div className="flex w-60 shrink-0 flex-col border-r bg-background">
          <SettingsNav groups={groups} current={current} onPick={onPick} />
          <div className="space-y-2 border-t px-4 py-3.5">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="font-semibold text-muted-foreground">版本与更新</span>
              {appUpdate?.current && (
                <span className="font-mono text-muted-foreground">当前版本 v{formatVersion(appUpdate.current)}</span>
              )}
            </div>
            <Button variant="outline" size="sm" className="w-full" disabled={checkingUpdate} onClick={checkAppUpdate}>
              {checkingUpdate && <LoaderIcon className="animate-spin" />}
              {checkingUpdate ? "正在检查…" : "检查更新"}
            </Button>
            {updateFetchError ? (
              <p className="text-xs text-destructive">{updateFetchError}</p>
            ) : appUpdate?.error ? (
              <p className="text-xs text-destructive">{appUpdate.error}</p>
            ) : appUpdate?.available && appUpdate.latest ? (
              <div className="space-y-2">
                <p className="text-xs font-medium text-primary">发现新版本 v{formatVersion(appUpdate.latest)}</p>
                {appUpdate.notes && (
                  <p className="line-clamp-3 text-xs text-muted-foreground">{appUpdate.notes}</p>
                )}
                {inApp && (
                  <Button size="sm" className="w-full" disabled={installingUpdate} onClick={installAppUpdate}>
                    {installingUpdate && <LoaderIcon className="animate-spin" />}
                    {installingUpdate ? (installStage || "正在更新…") : "下载并安装更新"}
                  </Button>
                )}
                {installError && <p className="text-xs text-destructive">{installError}</p>}
                {appUpdate.url && (
                  <Button variant="outline" size="sm" className="w-full" asChild>
                    <a href={appUpdate.url} target="_blank" rel="noreferrer">
                      前往下载新版本
                      <ExternalLinkIcon className="size-3.5" />
                    </a>
                  </Button>
                )}
              </div>
            ) : updateCheckedFresh && appUpdate ? (
              <p className="text-xs text-muted-foreground">已是最新版本</p>
            ) : null}
          </div>
        </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {current === QUOTA ? <QuotaPane />
            : group ? <GroupPane key={group.id} group={group} onSaved={onSaved} />
            : <div className="flex flex-1 items-center justify-center bg-canvas text-sm text-muted-foreground">正在读取设置…</div>}
        </div>
      </div>
    </div>
  )
}
