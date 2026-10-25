import * as React from "react"
import { PlusIcon, XIcon } from "lucide-react"

import { Card, Empty, PageBody, PageHeader, SectionTitle } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { errorMessage, glossaryApi, type BuiltinTerm, type BuiltinTerms, type GlossaryEntry } from "@/lib/api"
import { cn } from "@/lib/utils"

const SOURCE = { auto: "自动发现", agent: "Agent 建议", user: "" } as Record<string, string>

/**
 * The glossary searches expand (glossary.py): entries in use, and the
 * suggestions a deep search found in abstracts or the agent added, which count
 * only once the user confirms them.
 */
export function GlossaryPage({ onBack }: { onBack: () => void }) {
  const [data, setData] = React.useState<{ active: GlossaryEntry[]; suggested: GlossaryEntry[] } | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [tab, setTab] = React.useState("mine")
  const load = React.useCallback(() => {
    glossaryApi.list().then((d) => { setData(d); setError(null) }).catch((e) => setError(errorMessage(e)))
  }, [])
  React.useEffect(load, [load])
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title="术语表" active={tab} onTab={setTab}
        tabs={[{ id: "mine", label: "我的术语", count: data?.suggested.length }, { id: "builtin", label: "内置" }]}>
        <Button size="sm" variant="outline" onClick={onBack}>返回筛选</Button>
      </PageHeader>
      {tab === "builtin" ? <BuiltinTab /> : (
      <PageBody>
        {error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        {!data && !error && <p className="text-sm text-muted-foreground">正在读取术语表…</p>}
        {data && data.suggested.length > 0 && (
          <section>
            <SectionTitle title={`待确认（${data.suggested.length}）`} note="确认后才用于检索" />
            <Card>
              <ul className="divide-y">
                {data.suggested.map((e) => <EntryRow key={e.id} entry={e} onChanged={load} />)}
              </ul>
            </Card>
          </section>
        )}
        {data && (
          <section>
            <SectionTitle title="使用中" note="检索时同时搜索同一术语的各种写法" />
            <Card>
              {data.active.length === 0
                ? <Empty title="暂无术语" note="添加的术语和确认过的建议将显示在此处" />
                : <ul className="divide-y">{data.active.map((e) => <EntryRow key={e.id} entry={e} onChanged={load} />)}</ul>}
            </Card>
          </section>
        )}
        {data && (
          <section>
            <SectionTitle title="添加术语" />
            <Card className="p-5">
              <TermsEditor key={data.active.length} submit="添加"
                onSubmit={(terms, note) => glossaryApi.add(terms, note).then(load)} />
            </Card>
          </section>
        )}
      </PageBody>
      )}
    </div>
  )
}

/**
 * The built-in wordings: chemical elements (symbol and name) and British and
 * American spellings. Read only, but each can be switched off when its
 * expansion goes wrong for the user's field.
 */
function BuiltinTab() {
  const [q, setQ] = React.useState("")
  const [data, setData] = React.useState<BuiltinTerms | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const load = React.useCallback((query: string) => {
    glossaryApi.builtin(query).then((d) => { setData(d); setError(null) }).catch((e) => setError(errorMessage(e)))
  }, [])
  React.useEffect(() => {
    const t = setTimeout(() => load(q), 200)
    return () => clearTimeout(t)
  }, [q, load])
  const toggle = (t: BuiltinTerm) =>
    glossaryApi.switchBuiltin(t.id, t.disabled).then(() => load(q)).catch((e) => setError(errorMessage(e)))
  const spelling = q.trim() ? data?.spelling ?? [] : data?.spelling_disabled ?? []
  return (
    <PageBody>
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索元素或单词，例如 Al、aluminium"
        aria-label="搜索内置术语" className="bg-background" />
      {error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
      <section>
        <SectionTitle title="英美拼写" note="检索时同时搜索英式和美式拼写（VarCon）" />
        <Card>
          {spelling.length
            ? <ul className="divide-y">{spelling.map((t) => <BuiltinRow key={t.id} term={t} onToggle={toggle} />)}</ul>
            : <Empty title={q.trim() ? "没有匹配的单词" : "未停用任何拼写"} note={q.trim() ? undefined : "输入单词以查看它的英美拼写"} />}
        </Card>
      </section>
      <section>
        <SectionTitle title="元素" note="符号和名称互相展开；La、In 等与常用词同形的符号只换成名称" />
        <Card>
          {data?.elements.length
            ? <ul className="divide-y">{data.elements.map((t) => <BuiltinRow key={t.id} term={t} onToggle={toggle} />)}</ul>
            : <Empty title={data ? "没有匹配的元素" : "正在读取…"} />}
        </Card>
      </section>
    </PageBody>
  )
}

function BuiltinRow({ term: t, onToggle }: { term: BuiltinTerm; onToggle: (t: BuiltinTerm) => void }) {
  return (
    <li className="flex items-center gap-4 px-5 py-2">
      <div className={cn("flex min-w-0 flex-1 flex-wrap items-center gap-1.5", t.disabled && "text-muted-foreground")}>
        {t.terms.map((w, i) => (
          <span key={w} className={cn("rounded border px-1.5 py-px text-sm", i === 0 && "font-medium", t.disabled && "line-through")}>{w}</span>
        ))}
        {t.disabled && <span className="text-xs">已停用</span>}
      </div>
      <Button size="sm" variant="ghost" onClick={() => onToggle(t)}>{t.disabled ? "启用" : "停用"}</Button>
    </li>
  )
}

function EntryRow({ entry: e, onChanged }: { entry: GlossaryEntry; onChanged: () => void }) {
  const [mode, setMode] = React.useState<"view" | "edit" | "delete">("view")
  const [error, setError] = React.useState<string | null>(null)
  const act = (p: Promise<unknown>) => p.then(onChanged).catch((x) => setError(errorMessage(x)))
  const suggested = e.status === "suggested"
  if (mode === "edit") {
    return (
      <li className="px-5 py-4">
        <TermsEditor terms={e.terms} note={e.note} submit="保存" onCancel={() => setMode("view")}
          onSubmit={(terms, note) => glossaryApi.update(e.id, { terms, note }).then(() => { setMode("view"); onChanged() })} />
      </li>
    )
  }
  const meta = [SOURCE[e.source], e.added].filter(Boolean).join(" · ")
  return (
    <li className="flex items-start gap-4 px-5 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap gap-1.5">
          {e.terms.map((t, i) => (
            <span key={t} className={cn("rounded border px-1.5 py-px text-sm", i === 0 && "font-medium")}>{t}</span>
          ))}
        </div>
        {e.note && <div className="mt-1 text-xs text-muted-foreground">{e.note}</div>}
        <div className="mt-0.5 text-xs text-muted-foreground">{meta}</div>
        {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      </div>
      {mode === "delete" ? (
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-sm">删除此术语？</span>
          <Button size="sm" variant="destructive" onClick={() => act(glossaryApi.remove(e.id))}>删除</Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("view")}>保留</Button>
        </div>
      ) : (
        <div className="flex shrink-0 items-center gap-1">
          {suggested && <Button size="sm" variant="outline" onClick={() => act(glossaryApi.update(e.id, { confirm: true }))}>确认</Button>}
          <Button size="sm" variant="ghost" onClick={() => setMode("edit")}>修改</Button>
          {suggested
            ? <Button size="sm" variant="ghost" onClick={() => act(glossaryApi.remove(e.id))}>忽略</Button>
            : <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => setMode("delete")}>删除</Button>}
        </div>
      )}
    </li>
  )
}

const PLACEHOLDER = ["缩写，例如 PDC", "全称，例如 polymer-derived ceramic"]

/** The wordings of one entry, one per field, and a note; at least two wordings. */
function TermsEditor({ terms: initial, note: initialNote = "", submit, onSubmit, onCancel }: {
  terms?: string[]; note?: string; submit: string
  onSubmit: (terms: string[], note: string) => Promise<unknown>; onCancel?: () => void
}) {
  const [terms, setTerms] = React.useState<string[]>(initial?.length ? initial : ["", ""])
  const [note, setNote] = React.useState(initialNote)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const filled = terms.map((t) => t.trim()).filter(Boolean)
  const save = () => {
    if (filled.length < 2) { setError("至少填写两种写法"); return }
    setBusy(true)
    onSubmit(filled, note.trim()).catch((x) => setError(errorMessage(x))).finally(() => setBusy(false))
  }
  const change = (i: number, v: string) => { setTerms((ts) => ts.map((t, j) => (j === i ? v : t))); setError(null) }
  const onKey = (ev: React.KeyboardEvent) => { if (ev.key === "Enter") save() }
  return (
    <div className="space-y-2">
      {terms.map((t, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input value={t} onChange={(ev) => change(i, ev.target.value)} onKeyDown={onKey}
            placeholder={PLACEHOLDER[i] ?? "其他写法"} aria-label={`写法 ${i + 1}`} />
          {terms.length > 2 && (
            <Button size="icon-sm" variant="ghost" aria-label="移除此写法" title="移除此写法"
              onClick={() => setTerms((ts) => ts.filter((_, j) => j !== i))}>
              <XIcon />
            </Button>
          )}
        </div>
      ))}
      <Button size="sm" variant="ghost" className="-ml-2" onClick={() => setTerms((ts) => [...ts, ""])}>
        <PlusIcon />添加写法
      </Button>
      <Input value={note} onChange={(ev) => setNote(ev.target.value)} onKeyDown={onKey}
        placeholder="备注，例如适用的领域" aria-label="备注" />
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex gap-2 pt-1">
        <Button size="sm" onClick={save} disabled={busy}>{submit}</Button>
        {onCancel && <Button size="sm" variant="ghost" onClick={onCancel}>放弃修改</Button>}
      </div>
    </div>
  )
}
