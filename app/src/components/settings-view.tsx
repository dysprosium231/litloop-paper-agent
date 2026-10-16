import * as React from "react"
import { CheckCircle2Icon, CircleDashedIcon, ExternalLinkIcon, LoaderIcon, XCircleIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { SidebarContent, SidebarGroup, SidebarGroupContent, SidebarHeader } from "@/components/ui/sidebar"
import { Switch } from "@/components/ui/switch"
import { settingsApi, type ProviderUsage, type SettingField, type SettingGroup, type TestResult } from "@/lib/api"
import { cn } from "@/lib/utils"

export const QUOTA = "quota"

/** Second column on the settings section: the groups, plus API usage. */
export function SettingsNav({ groups, current, onPick }: { groups: SettingGroup[]; current: string; onPick: (id: string) => void }) {
  const entries = [...groups.map((g) => ({ id: g.id, title: g.title })), { id: QUOTA, title: "接口额度" }]
  return (
    <>
      <SidebarHeader className="border-b p-4">
        <div className="text-base font-medium text-foreground">设置</div>
      </SidebarHeader>
      <SidebarContent className="scrollable">
        <SidebarGroup className="px-0">
          <SidebarGroupContent>
            {entries.map((e) => (
              <button key={e.id} type="button" onClick={() => onPick(e.id)}
                className={cn("flex w-full flex-col items-start gap-1 border-b p-4 text-left text-sm whitespace-normal hover:bg-sidebar-accent",
                  e.id === current && "bg-sidebar-accent text-sidebar-accent-foreground")}>
                <span className="font-medium">{e.title}</span>
              </button>
            ))}
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </>
  )
}

function ResultIcon({ ok }: { ok: boolean | null }) {
  if (ok === true) return <CheckCircle2Icon className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
  if (ok === false) return <XCircleIcon className="size-4 shrink-0 text-destructive" />
  return <CircleDashedIcon className="size-4 shrink-0 text-muted-foreground" />
}

const SOURCE: Record<SettingField["source"], string> = { file: "", env: "来自环境变量", default: "默认值" }

/** One setting: a field that saves on Enter / leaving it, a switch or a choice that save at once. */
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
      setState(String(e).replace(/^Error: \d+ /, ""))
    }
  }
  const id = `setting-${field.key}`

  let control: React.ReactNode
  if (field.type === "bool") {
    control = <Switch id={id} checked={Boolean(field.value)} onCheckedChange={(v) => save(v)} />
  } else if (field.type === "select") {
    control = (
      <Select value={String(field.value ?? "")} onValueChange={(v) => save(v)}>
        <SelectTrigger id={id} className="w-64"><SelectValue /></SelectTrigger>
        <SelectContent>
          {field.options?.map(([v, label]) => <SelectItem key={v} value={v}>{label}</SelectItem>)}
        </SelectContent>
      </Select>
    )
  } else if (field.type === "secret" && !editing) {
    control = (
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm">{field.set ? field.value : <span className="text-muted-foreground">未填写</span>}</span>
        <Button variant="outline" size="sm" onClick={() => setEditing(true)}>{field.set ? "更换" : "填写"}</Button>
        {field.set && field.source === "file" && <Button variant="ghost" size="sm" onClick={() => save("")}>清除</Button>}
      </div>
    )
  } else {
    control = (
      <form className="flex w-full max-w-xl gap-2" onSubmit={(e) => { e.preventDefault(); save(draft) }}>
        <Input id={id} type={field.type === "secret" ? "password" : field.type === "email" ? "email" : "text"}
          autoComplete="off" spellCheck={false} value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder={field.placeholder ?? (field.type === "secret" ? "粘贴新的 Key" : field.type === "email" ? "name@example.edu" : "")}
          autoFocus={field.type === "secret"} />
        <Button type="submit" size="sm" className="h-9">保存</Button>
        {field.type === "secret" && <Button type="button" variant="ghost" size="sm" className="h-9" onClick={() => setEditing(false)}>取消</Button>}
      </form>
    )
  }

  return (
    <div className={cn("space-y-2 border-b py-4 last:border-b-0", field.warn && field.value && "rounded-md")}>
      <div className="flex items-center gap-2">
        <Label htmlFor={id} className="text-sm font-medium">{field.label}</Label>
        {SOURCE[field.source] && field.set && <Badge variant="secondary" className="font-normal">{SOURCE[field.source]}{field.env ? ` ${field.env}` : ""}</Badge>}
        {state === "saving" && <LoaderIcon className="size-3.5 animate-spin text-muted-foreground" />}
        {state === "saved" && <span className="text-xs text-emerald-600 dark:text-emerald-400">已保存</span>}
        {field.link && (
          <a href={field.link} target="_blank" rel="noreferrer" className="ml-auto flex items-center gap-1 text-xs text-primary hover:underline">
            申请<ExternalLinkIcon className="size-3" />
          </a>
        )}
      </div>
      {control}
      {field.help && <p className={cn("text-xs", field.warn ? "text-warning-foreground" : "text-muted-foreground")}>{field.help}</p>}
      {!["idle", "saving", "saved"].includes(state) && <p className="text-xs text-destructive">{state}</p>}
    </div>
  )
}

function GroupPane({ group, onSaved }: { group: SettingGroup; onSaved: (f: SettingField) => void }) {
  const [results, setResults] = React.useState<TestResult[] | null>(null)
  const [testing, setTesting] = React.useState(false)
  React.useEffect(() => setResults(null), [group.id])
  const test = async () => {
    setTesting(true)
    try {
      setResults((await settingsApi.test(group.test!)).results)
    } catch (e) {
      setResults([{ name: "检测", ok: false, detail: String(e) }])
    } finally {
      setTesting(false)
    }
  }
  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6">
      <div className="flex items-start gap-4">
        <h1 className="text-lg font-semibold">{group.title}</h1>
        {group.test && (
          <Button variant="outline" className="ml-auto shrink-0" onClick={test} disabled={testing}>
            {testing && <LoaderIcon className="animate-spin" />}检测
          </Button>
        )}
      </div>
      {results && (
        <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
          {results.map((r) => (
            <div key={r.name} className="flex items-start gap-2 text-sm">
              <ResultIcon ok={r.ok} />
              <span className="w-28 shrink-0 font-medium">{r.name}</span>
              <span className="text-muted-foreground">{r.detail}{r.cost && <span className="ml-1 text-xs">（{r.cost}）</span>}</span>
            </div>
          ))}
        </div>
      )}
      <div>{group.fields.map((f) => <Field key={f.key} field={f} onSaved={onSaved} />)}</div>
    </div>
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
    <div className="mx-auto w-full max-w-3xl space-y-5 px-6 py-6">
      <div className="flex items-start gap-4">
        <h1 className="text-lg font-semibold">接口额度</h1>
        <Button variant="outline" className="ml-auto shrink-0" onClick={load}>刷新</Button>
      </div>
      {live && !live.error && (
        <div className="space-y-2 rounded-lg border p-4">
          <div className="flex items-baseline justify-between text-sm">
            <span className="font-medium">OpenAlex 今日剩余</span>
            <span>{live.credits_remaining?.toLocaleString()} / {live.credits_limit?.toLocaleString()} 积分</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div className={cn("h-full rounded-full", pct !== null && pct < 0.2 ? "bg-amber-500" : "bg-primary")} style={{ width: `${(pct ?? 0) * 100}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">重置 {live.resets_at ? new Date(live.resets_at).toLocaleString() : "未知"}</p>
        </div>
      )}
      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs text-muted-foreground">
            <tr><th className="p-3 text-left font-normal">接口</th><th className="p-3 text-right font-normal">今日请求</th><th className="p-3 text-right font-normal">剩余</th><th className="p-3 text-right font-normal">被限流</th><th className="p-3 text-left font-normal">重置</th></tr>
          </thead>
          <tbody>
            {usage && Object.entries(usage).map(([name, u]) => (
              <tr key={name} className="border-t">
                <td className="p-3">{PROVIDERS[name] ?? name}</td>
                <td className="p-3 text-right tabular-nums">{u.requests_today}</td>
                <td className="p-3 text-right tabular-nums">{u.remaining == null ? "—" : `${u.remaining.toLocaleString()} / ${u.limit?.toLocaleString() ?? "?"}`}</td>
                <td className={cn("p-3 text-right tabular-nums", u.throttled_today > 0 && "text-warning-foreground")}>{u.throttled_today}</td>
                <td className="p-3 text-muted-foreground">{u.resets_at ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Main area on the settings section. */
export function SettingsPane({ groups, current, onSaved }: { groups: SettingGroup[]; current: string; onSaved: (f: SettingField) => void }) {
  if (current === QUOTA) return <div className="min-h-0 flex-1 overflow-auto"><QuotaPane /></div>
  const group = groups.find((g) => g.id === current)
  if (!group) return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">正在读取设置…</div>
  return <div className="min-h-0 flex-1 overflow-auto"><GroupPane group={group} onSaved={onSaved} /></div>
}
