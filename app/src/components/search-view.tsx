import * as React from "react"
import { LoaderIcon } from "lucide-react"

import { CollectionPicker } from "@/components/collection-picker"
import { Card, Empty, PageBody, PageHeader, SectionTitle } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  downloadsApi, errorMessage, searchApi, type SearchForm, type SearchPlan, type SearchRun, type ZoteroCollection,
} from "@/lib/api"
import { cn } from "@/lib/utils"

const EMPTY: SearchForm = {
  question: "", criteria: "", mode: "quick", limit: 20, batches: 0,
  year_from: null, year_to: null, max_cas_zone: null, max_jcr_quartile: null, exclude_warning: false,
  seeds: "", seed_collection: "",
}
const STATE: Record<SearchRun["state"], string> = {
  running: "进行中", done: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断",
}
const textarea = "w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"

/**
 * A new search from the app: the form is sent to the agent as a message, the way
 * the user would type it in a chat; the agent searches, rates and saves a
 * screening session, which then opens here like any other.
 */
export function SearchPage({ onBack, onOpen }: { onBack: () => void; onOpen: (session: string) => void }) {
  const [form, setForm] = React.useState<SearchForm>(EMPTY)
  const [runs, setRuns] = React.useState<SearchRun[] | null>(null)
  const [live, setLive] = React.useState<SearchRun | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [collections, setCollections] = React.useState<ZoteroCollection[] | null>(null)
  const [planning, setPlanning] = React.useState(false)
  const [plan, setPlan] = React.useState<SearchPlan | null>(null)
  const [planQueries, setPlanQueries] = React.useState("")
  const [planCoreTerms, setPlanCoreTerms] = React.useState("")
  const set = <K extends keyof SearchForm>(k: K, v: SearchForm[K]) => setForm((f) => ({ ...f, [k]: v }))

  const refresh = React.useCallback(() => searchApi.list().then(setRuns).catch((e) => setError(errorMessage(e))), [])
  React.useEffect(() => { refresh() }, [refresh])
  const running = runs?.find((r) => r.state === "running") ?? null
  // Follow the running search: what the agent is doing now.
  React.useEffect(() => {
    if (!running) { setLive(null); return }
    let stop = false
    const tick = () => searchApi.get(running.run).then((r) => {
      if (stop) return
      setLive(r)
      if (r.state !== "running") refresh()
    }).catch(() => {})
    tick()
    const t = setInterval(tick, 2000)
    return () => { stop = true; clearInterval(t) }
  }, [running?.run, refresh])
  React.useEffect(() => {
    if (form.mode === "deep" && collections === null) downloadsApi.collections().then(setCollections).catch(() => setCollections([]))
  }, [form.mode, collections])

  const start = (usePlan = false) => {
    setError(null)
    const payload: SearchForm = usePlan && plan
      ? {
          ...form,
          planned_queries: planQueries.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
          planned_core_terms: planCoreTerms.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
        }
      : form
    searchApi.start(payload).then((r) => {
      if (r.state === "failed") setError(r.error)
      else if (usePlan) setPlan(null)
      refresh()
    }).catch((e) => setError(errorMessage(e)))
  }
  const previewPlan = () => {
    setError(null)
    setPlanning(true)
    searchApi.plan(form).then((p) => {
      setPlan(p)
      setPlanQueries(p.queries.join("\n"))
      setPlanCoreTerms(p.core_terms.join("\n"))
      if (!form.criteria.trim() && p.criteria.length > 0) {
        set("criteria", p.criteria.join("\n"))
      }
    }).catch((e) => setError(errorMessage(e))).finally(() => setPlanning(false))
  }
  const resume = (session: string) => {
    setError(null)
    searchApi.resume(session).then((r) => {
      if (r.state === "failed") setError(r.error)
      refresh()
    }).catch((e) => setError(errorMessage(e)))
  }
  const year = (v: string) => (v.trim() ? Number(v) : null)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="新建检索">
        <Button size="sm" variant="outline" onClick={onBack}>返回筛选</Button>
      </PageHeader>
      <PageBody>
        <Card className="space-y-5 p-5">
          <Field label="检索问题" htmlFor="q">
            <textarea id="q" rows={2} className={textarea} value={form.question} onChange={(e) => set("question", e.target.value)}
              placeholder="例如：SiC 颗粒增强铝基复合材料的界面反应与力学性能" />
          </Field>
          <Field label="纳入标准" htmlFor="c" note="每行一条；留空时由 Agent 拟定">
            <textarea id="c" rows={3} className={textarea} value={form.criteria} onChange={(e) => set("criteria", e.target.value)} />
          </Field>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="方式">
              <div className="inline-flex rounded-md border p-0.5">
                {(["quick", "deep"] as const).map((m) => (
                  <button key={m} type="button" onClick={() => set("mode", m)}
                    className={cn("rounded px-3 py-1 text-sm", form.mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
                    {m === "quick" ? "快速检索" : "深度检索"}
                  </button>
                ))}
              </div>
            </Field>
            {form.mode === "quick" ? (
              <Field label="篇数">
                <Pick value={String(form.limit)} onChange={(v) => set("limit", Number(v))}
                  options={[["20", "约 20 篇"], ["50", "约 50 篇"], ["100", "约 100 篇"]]} />
              </Field>
            ) : (
              <Field label="规模">
                <Pick value={String(form.batches)} onChange={(v) => set("batches", Number(v))}
                  options={[["0", "直到建议停止"], ["1", "1 批（约 20 篇）"], ["3", "最多 3 批"], ["5", "最多 5 批"]]} />
              </Field>
            )}
          </div>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="年份">
              <div className="flex items-center gap-2">
                <Input className="w-24" inputMode="numeric" placeholder="不限" value={form.year_from ?? ""} onChange={(e) => set("year_from", year(e.target.value))} />
                <span className="text-muted-foreground">–</span>
                <Input className="w-24" inputMode="numeric" placeholder="至今" value={form.year_to ?? ""} onChange={(e) => set("year_to", year(e.target.value))} />
              </div>
            </Field>
            <Field label="期刊">
              <div className="flex flex-wrap items-center gap-2">
                <Pick value={String(form.max_cas_zone ?? 0)} onChange={(v) => set("max_cas_zone", Number(v) || null)} className="w-36"
                  options={[["0", "中科院分区不限"], ["1", "中科院 1 区"], ["2", "中科院 2 区及以上"], ["3", "中科院 3 区及以上"]]} />
                <Pick value={String(form.max_jcr_quartile ?? 0)} onChange={(v) => set("max_jcr_quartile", Number(v) || null)} className="w-32"
                  options={[["0", "JCR 不限"], ["1", "JCR Q1"], ["2", "JCR Q2 及以上"], ["3", "JCR Q3 及以上"]]} />
              </div>
              <label className="mt-2 flex items-center gap-2 text-sm">
                <Switch checked={form.exclude_warning} onCheckedChange={(v) => set("exclude_warning", v)} />排除预警期刊
              </label>
            </Field>
          </div>
          {form.mode === "deep" && (
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label="种子文献" htmlFor="s" note="确定相关的 1–3 篇，每行一个 DOI；留空时由 Agent 先检索">
                <textarea id="s" rows={3} className={textarea} value={form.seeds} onChange={(e) => set("seeds", e.target.value)}
                  placeholder="10.1016/j.compositesa.2020.105967" />
              </Field>
              <Field label="Zotero 分类作种子" note="分类里有 DOI 的文献都作种子">
                <CollectionPicker collections={collections} placeholder="不使用"
                  value={collections?.find((c) => c.path === form.seed_collection)?.key ?? ""}
                  onChange={(key) => set("seed_collection", collections?.find((c) => c.key === key)?.path ?? "")} />
                {form.seed_collection && (
                  <button type="button" className="mt-1 text-xs text-muted-foreground hover:text-foreground" onClick={() => set("seed_collection", "")}>
                    不使用分类
                  </button>
                )}
              </Field>
            </div>
          )}
          {error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" variant="outline" onClick={previewPlan} disabled={!form.question.trim() || Boolean(running) || planning}>
              {planning ? <><LoaderIcon className="animate-spin" />正在生成方案…</> : "预览检索方案"}
            </Button>
            <Button onClick={() => start(false)} disabled={!form.question.trim() || Boolean(running)}>开始检索</Button>
            <span className="text-xs text-muted-foreground">
              {running ? "已有检索在进行，完成后再开始新的检索" : "可先预览检索词和预计积分，再确认开始"}
            </span>
          </div>
        </Card>

        {plan && (
          <Card className="space-y-4 border-primary/30 p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold">检索方案预览</h3>
                <p className="text-xs text-muted-foreground">
                  模型：{plan.model} · 预计消耗 {plan.estimated_credits}（{plan.credit_note}）
                  {plan.usage && (
                    <>
                      {" · 本次规划 Token "}
                      <span className="tabular-nums">{plan.usage.total_tokens.toLocaleString()}</span>
                      {plan.usage.cost_text ? `（${plan.usage.cost_text}）` : ""}
                    </>
                  )}
                </p>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setPlan(null)}>收起方案</Button>
            </div>
            <Field label="检索词" htmlFor="pq" note="每行一条英文检索式，可直接修改">
              <textarea id="pq" rows={4} className={textarea} value={planQueries} onChange={(e) => setPlanQueries(e.target.value)} />
            </Field>
            <Field label="核心词" htmlFor="pc" note="每行一条核心概念词，同义词可用 | 分隔">
              <textarea id="pc" rows={2} className={textarea} value={planCoreTerms} onChange={(e) => setPlanCoreTerms(e.target.value)} />
            </Field>
            <div className="flex items-center gap-3">
              <Button onClick={() => start(true)} disabled={Boolean(running) || !planQueries.trim()}>按此方案开始检索</Button>
              <span className="text-xs text-muted-foreground">检索词与上面的纳入标准会一起发给 Agent</span>
            </div>
          </Card>
        )}

        <SectionTitle title="最近的检索" />
        <Card>
          {runs && runs.length === 0 && <Empty title="暂无从 app 发起的检索" />}
          <ul className="divide-y">
            {runs?.map((r) => {
              const cur = live && live.run === r.run ? live : r
              return <RunRow key={r.run} run={cur} busy={Boolean(running)} onOpen={onOpen}
                onResume={() => r.session && resume(r.session)}
                onCancel={() => searchApi.cancel(r.run).then(refresh)} />
            })}
          </ul>
        </Card>
      </PageBody>
    </div>
  )
}

function RunRow({ run: r, busy, onOpen, onResume, onCancel }: {
  run: SearchRun; busy: boolean; onOpen: (s: string) => void; onResume: () => void; onCancel: () => void
}) {
  const [showDetail, setShowDetail] = React.useState(false)
  const [copiedPrompt, setCopiedPrompt] = React.useState(false)
  const steps = r.events?.slice(-4) ?? []
  const hasDetail = Boolean(r.prompt || r.agent_session || r.usage)

  const copyPrompt = () => {
    if (!r.prompt) return
    navigator.clipboard.writeText(r.prompt).then(() => {
      setCopiedPrompt(true)
      setTimeout(() => setCopiedPrompt(false), 1500)
    }).catch(() => {})
  }

  return (
    <li className="space-y-1.5 px-5 py-3">
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{r.question}</span>
        {r.harness_label && (
          <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
            {r.harness_label}{r.resumed_chat ? " · 续接" : ""}
          </span>
        )}
        <span className="shrink-0 rounded border px-1.5 text-xs text-muted-foreground">{r.mode === "deep" ? "深度检索" : "快速检索"}</span>
        <span className={cn("shrink-0 text-xs", r.state === "failed" ? "text-destructive" : "text-muted-foreground")}>{STATE[r.state]}</span>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{r.started.slice(5, 16).replace("T", " ")}</span>
        {r.state !== "running" && r.usage && (
          <span
            className="shrink-0 text-xs tabular-nums text-muted-foreground"
            title={`输入 ${r.usage.input_tokens.toLocaleString()} / 输出 ${r.usage.output_tokens.toLocaleString()}`}
          >
            Token {r.usage.total_tokens.toLocaleString()}{r.usage.cost_text ? ` (${r.usage.cost_text})` : ""}
          </span>
        )}
        {hasDetail && (
          <Button size="sm" variant="ghost" onClick={() => setShowDetail((v) => !v)}>
            {showDetail ? "收起对话详情" : "查看对话详情"}
          </Button>
        )}
        {r.state === "running" && <Button size="sm" variant="ghost" onClick={onCancel}>取消检索</Button>}
        {r.mode === "deep" && r.session && r.state !== "running" && (
          <Button size="sm" variant="outline" disabled={busy} onClick={onResume}
            title={busy ? "已有检索在进行，完成后再继续" : "从上次停下的地方继续深度检索"}>继续检索</Button>
        )}
        {r.session && r.state !== "running" && <Button size="sm" variant="outline" onClick={() => onOpen(r.session!)}>查看结果</Button>}
      </div>
      {r.state === "running" && (
        <div className="flex items-center gap-1.5 text-xs text-primary">
          <LoaderIcon className="size-3 animate-spin" />{r.doing ?? "Agent 正在检索…"}
        </div>
      )}
      {r.state === "running" && steps.length > 0 && (
        <ol className="space-y-0.5 text-xs text-muted-foreground">
          {steps.map((e, i) => <li key={i}><span className="tabular-nums">{e.t}</span> {e.text}</li>)}
        </ol>
      )}
      {r.result && r.state !== "running" && !showDetail && <p className="whitespace-pre-line text-xs text-muted-foreground">{r.result}</p>}
      {r.error && <p className="text-xs text-destructive">{r.error}</p>}
      {showDetail && hasDetail && (
        <div className="mt-2 space-y-2.5 rounded-lg border bg-muted/30 p-3 text-xs">
          {r.usage && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
              <span>
                Token 消耗：<span className="font-medium tabular-nums text-foreground">{r.usage.total_tokens.toLocaleString()}</span>
                （输入 {r.usage.input_tokens.toLocaleString()} / 输出 {r.usage.output_tokens.toLocaleString()}）
              </span>
              {r.usage.cost_text && (
                <span>
                  参考费用：<span className="font-medium text-foreground">{r.usage.cost_text}</span>
                </span>
              )}
            </div>
          )}
          {r.agent_session && (
            <div className="flex items-center gap-2">
              <span className="shrink-0 text-muted-foreground">会话 ID：</span>
              <code className="font-mono text-foreground">{r.agent_session}</code>
            </div>
          )}
          {r.prompt && (
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="font-medium text-muted-foreground">发送给 Agent 的提示词</span>
                <button
                  type="button"
                  onClick={copyPrompt}
                  className="rounded px-1.5 py-0.5 text-xs text-primary hover:bg-primary/10"
                >
                  {copiedPrompt ? "已复制" : "复制提示词"}
                </button>
              </div>
              <pre className="max-h-48 overflow-auto rounded-md border bg-background p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-foreground">
                {r.prompt}
              </pre>
            </div>
          )}
          {r.result && (
            <div className="space-y-1">
              <div className="font-medium text-muted-foreground">Agent 最终回复</div>
              <div className="rounded-md border bg-background p-2.5 leading-relaxed whitespace-pre-line text-foreground">
                {r.result}
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

function Field({ label, htmlFor, note, children }: { label: string; htmlFor?: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}

function Pick({ value, onChange, options, className }: {
  value: string; onChange: (v: string) => void; options: [string, string][]; className?: string
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className={cn("w-44", className)}><SelectValue /></SelectTrigger>
      <SelectContent>{options.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectContent>
    </Select>
  )
}
