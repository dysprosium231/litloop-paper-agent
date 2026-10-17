import * as React from "react"
import { CheckCircle2Icon, LoaderIcon, XCircleIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { CollectionPicker } from "@/components/collection-picker"
import { Card, Empty, PageBody, SectionTitle } from "@/components/page"
import { downloadsApi, zoteroApi, type ZoteroCheckItem, type ZoteroCollection, errorMessage } from "@/lib/api"

const STATE: Record<ZoteroCheckItem["state"], string> = { ok: "附件完整", missing: "附件缺失", empty: "附件为空" }

type Outcome = { state: "working" | "done" | "error"; text?: string }

/**
 * Attachment check: list the items of a collection whose PDF is missing or empty,
 * and after the user's choice attach the local PDF (empty attachments go to the
 * Zotero Trash first) or download the rest. Nothing changes before a button is pressed.
 */
export function ZoteroCheckPane({ running, onStarted }: { running: boolean; onStarted: (job: string) => void }) {
  const [collections, setCollections] = React.useState<ZoteroCollection[] | null>(null)
  const [key, setKey] = React.useState<string>("")
  const [items, setItems] = React.useState<ZoteroCheckItem[] | null>(null)
  const [checking, setChecking] = React.useState(false)
  const [picked, setPicked] = React.useState<Set<string>>(new Set())
  const [outcome, setOutcome] = React.useState<Record<string, Outcome>>({})
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (running) downloadsApi.collections().then(setCollections).catch((e) => setError(errorMessage(e)))
  }, [running])

  const path = collections?.find((c) => c.key === key)?.path ?? ""
  const problems = (items ?? []).filter((i) => i.state !== "ok")
  const chosen = problems.filter((i) => picked.has(i.key) && outcome[i.key]?.state !== "done")
  const toAttach = chosen.filter((i) => i.local_pdf)
  const toDownload = chosen.filter((i) => !i.local_pdf && i.doi)
  const busy = checking || Object.values(outcome).some((o) => o.state === "working")

  const check = async () => {
    setChecking(true)
    setError(null)
    setOutcome({})
    try {
      const r = await zoteroApi.check(key)
      setItems(r.items)
      setPicked(new Set(r.items.filter((i) => i.state !== "ok").map((i) => i.key)))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setChecking(false)
    }
  }

  const attach = async () => {
    for (const it of toAttach) {
      setOutcome((o) => ({ ...o, [it.key]: { state: "working" } }))
      try {
        await zoteroApi.repair(it, true)
        setOutcome((o) => ({ ...o, [it.key]: { state: "done", text: "已重新附加" } }))
      } catch (e) {
        setOutcome((o) => ({ ...o, [it.key]: { state: "error", text: errorMessage(e) } }))
      }
    }
  }

  const download = async () => {
    try {
      // Empty attachments would block the PDF the download brings; trash them first.
      for (const it of toDownload.filter((i) => i.empty_attachments.length)) await zoteroApi.repair(it, false)
      const job = await downloadsApi.start(
        toDownload.map((i) => ({ doi: i.doi, title: i.title, year: i.year, journal: i.journal })),
        `附件检查 · ${path}`, undefined, path)
      onStarted(job.job)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const toggle = (k: string) => setPicked((s) => {
    const n = new Set(s)
    if (n.has(k)) n.delete(k)
    else n.add(k)
    return n
  })

  const counts = items && `共 ${items.length} · 完整 ${items.length - problems.length}` +
    (problems.some((i) => i.state === "missing") ? ` · 附件缺失 ${problems.filter((i) => i.state === "missing").length}` : "") +
    (problems.some((i) => i.state === "empty") ? ` · 附件为空 ${problems.filter((i) => i.state === "empty").length}` : "")

  return (
    <PageBody wide>
      <Card className="space-y-3 p-5">
        <div>
          <h2 className="text-[15px] font-semibold">检测分类</h2>
          <div className="mt-0.5 text-xs text-muted-foreground">{counts || "找出分类中缺失或为空的 PDF 附件；检测只读取，不做修改"}</div>
        </div>
        <div className="flex items-center gap-2">
          <CollectionPicker collections={collections} value={key} onChange={(v) => { setKey(v); setItems(null) }}
            disabled={!running || !collections} placeholder={running ? (collections ? "选择分类" : "正在加载…") : "Zotero 未运行"} />
          <Button variant="outline" disabled={!key || busy} onClick={check}>
            {checking && <LoaderIcon className="animate-spin" />}检测
          </Button>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
      </Card>

      {items && problems.length === 0 && (
        <Card><Empty title="附件完整" note="此分类中的文献均有 PDF 附件" /></Card>
      )}

      {problems.length > 0 && (
        <section>
          <SectionTitle title="需要处理"
            note={chosen.some((i) => i.state === "empty") ? "附件为空的文献会先将空附件移到 Zotero 回收站" : `已选 ${chosen.length} 篇`}>
            <Button size="sm" variant="outline" disabled={busy || toDownload.length === 0} onClick={download}>
              加入下载（{toDownload.length}）
            </Button>
            <Button size="sm" disabled={busy || toAttach.length === 0} onClick={attach}>
              重新附加（{toAttach.length}）
            </Button>
          </SectionTitle>
          <Card>
            <ul className="divide-y">
              {problems.map((it) => {
                const o = outcome[it.key]
                return (
                  <li key={it.key} className="flex items-start gap-3 px-5 py-3">
                    <input type="checkbox" className="mt-1 size-4 accent-primary" aria-label={it.title}
                      checked={picked.has(it.key)} disabled={busy || o?.state === "done"} onChange={() => toggle(it.key)} />
                    <div className="min-w-0 flex-1">
                      <div className="line-clamp-2 text-sm">{it.title || it.doi}</div>
                      <div className="text-xs text-muted-foreground">
                        {[it.year, it.journal, STATE[it.state], it.local_pdf ? "本机有 PDF" : it.doi ? "需下载" : "无 DOI，无法下载"]
                          .filter(Boolean).join(" · ")}
                      </div>
                      {o?.text && <div className={o.state === "error" ? "mt-0.5 text-xs text-destructive" : "mt-0.5 text-xs text-muted-foreground"}>{o.text}</div>}
                    </div>
                    <span className="mt-0.5 shrink-0">
                      {o?.state === "working" && <LoaderIcon className="size-4 animate-spin text-muted-foreground" />}
                      {o?.state === "done" && <CheckCircle2Icon className="size-4 text-emerald-600 dark:text-emerald-400" />}
                      {o?.state === "error" && <XCircleIcon className="size-4 text-destructive" />}
                    </span>
                  </li>
                )
              })}
            </ul>
          </Card>
        </section>
      )}
    </PageBody>
  )
}
