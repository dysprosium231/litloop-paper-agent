import * as React from "react"
import { CheckIcon } from "lucide-react"

import { GroupPane } from "@/components/settings-view"
import { Button } from "@/components/ui/button"
import { settingsApi, type SettingField, type SettingGroup } from "@/lib/api"
import { cn } from "@/lib/utils"

/**
 * First-run guide (CHARTER: 初次使用引导): the settings groups in the order a new
 * user needs them, each with why it matters and its check. Any step can be
 * skipped; everything stays changeable on the settings page.
 */
export function Onboarding({ steps, onDone }: { steps: { group: string; intro: string }[]; onDone: () => void }) {
  const [groups, setGroups] = React.useState<SettingGroup[]>([])
  const [at, setAt] = React.useState(0)
  const [seen, setSeen] = React.useState<Set<number>>(new Set([0]))
  React.useEffect(() => { settingsApi.list().then(setGroups).catch(() => {}) }, [])
  const shown = steps.filter((s) => groups.some((g) => g.id === s.group))
  const step = shown[at]
  const group = groups.find((g) => g.id === step?.group)
  const go = (i: number) => {
    setAt(i)
    setSeen((s) => new Set(s).add(i))
  }
  const finish = () => { settingsApi.finishOnboarding().catch(() => {}).finally(onDone) }
  const onSaved = (f: SettingField) =>
    setGroups((gs) => gs.map((g) => ({ ...g, fields: g.fields.map((x) => (x.key === f.key ? f : x)) })))
  const last = at === shown.length - 1

  return (
    <div className="flex h-svh bg-background">
      <aside className="flex w-64 shrink-0 flex-col border-r bg-sidebar px-4 py-6">
        <div className="flex items-center gap-2.5 px-2">
          <img src="/litloop-icon.svg" alt="" className="size-8 rounded-lg" />
          <span className="text-base font-semibold">开始使用 LitLoop</span>
        </div>
        <p className="mt-3 px-2 text-[13px] leading-relaxed text-muted-foreground">逐项完成设置，每一步都可以先跳过，以后在「设置」中修改。</p>
        <ol className="mt-6 space-y-1">
          {shown.map((s, i) => {
            const title = groups.find((g) => g.id === s.group)?.title ?? s.group
            return (
              <li key={s.group}>
                <button type="button" onClick={() => go(i)}
                  className={cn("flex h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-accent",
                    i === at && "bg-primary/10 font-medium text-primary hover:bg-primary/10")}>
                  <span className={cn("flex size-5 shrink-0 items-center justify-center rounded-full border text-[11px] tabular-nums",
                    i === at ? "border-primary" : seen.has(i) ? "border-transparent bg-muted" : "")}>
                    {seen.has(i) && i !== at ? <CheckIcon className="size-3" /> : i + 1}
                  </span>
                  {title}
                </button>
              </li>
            )
          })}
        </ol>
        <button type="button" onClick={finish} className="mt-auto px-3 text-left text-xs text-muted-foreground hover:text-foreground">
          跳过引导
        </button>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col">
          {group && step
            ? <GroupPane key={group.id} group={group} intro={step.intro} onSaved={onSaved} />
            : <div className="flex flex-1 items-center justify-center bg-canvas text-sm text-muted-foreground">正在读取设置…</div>}
        </div>
        <footer className="flex shrink-0 items-center gap-2 border-t bg-background px-8 py-4">
          <span className="text-xs text-muted-foreground tabular-nums">第 {Math.min(at + 1, shown.length)} 步，共 {shown.length} 步</span>
          <div className="ml-auto flex gap-2">
            {at > 0 && <Button variant="ghost" onClick={() => go(at - 1)}>上一步</Button>}
            {!last && <Button variant="outline" onClick={() => go(at + 1)}>跳过此步</Button>}
            <Button className="min-w-24" onClick={() => (last ? finish() : go(at + 1))}>{last ? "完成" : "下一步"}</Button>
          </div>
        </footer>
      </div>
    </div>
  )
}
