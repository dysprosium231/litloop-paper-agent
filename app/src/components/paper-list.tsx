import * as React from "react"

import { RelevanceBadge } from "@/components/relevance-badge"
import { Label } from "@/components/ui/label"
import { SidebarContent, SidebarGroup, SidebarGroupContent, SidebarHeader, SidebarInput } from "@/components/ui/sidebar"
import { Switch } from "@/components/ui/switch"
import type { Relevance } from "@/lib/api"
import { cn } from "@/lib/utils"

export interface PaperRow {
  key: string
  title: string
  meta: string
  reason: string
  rel: Relevance | null // null: not rated yet
  edited: boolean
  selected: boolean
}

/** The second column on the screening section: this batch's papers. */
export function PaperList({
  rows,
  current,
  onPick,
  heading,
  onlyBatch,
  onOnlyBatch,
}: {
  rows: PaperRow[]
  current: string | null
  onPick: (key: string) => void
  heading: string
  onlyBatch: boolean
  onOnlyBatch: (v: boolean) => void
}) {
  const [query, setQuery] = React.useState("")
  const shown = query ? rows.filter((r) => (r.title + r.meta).toLowerCase().includes(query.toLowerCase())) : rows
  return (
    <>
      <SidebarHeader className="gap-3 border-b p-4">
        <div className="flex w-full items-center justify-between gap-2">
          <div className="min-w-0 truncate text-base font-medium text-foreground">{heading}</div>
          <Label className="flex shrink-0 items-center gap-2 text-sm">
            <span>只看本批</span>
            <Switch className="shadow-none" checked={onlyBatch} onCheckedChange={onOnlyBatch} />
          </Label>
        </div>
        <SidebarInput placeholder="搜索标题或期刊" value={query} onChange={(e) => setQuery(e.target.value)} />
      </SidebarHeader>
      <SidebarContent className="scrollable">
        <SidebarGroup className="px-0">
          <SidebarGroupContent>
            {shown.map((r) => (
              <button
                type="button"
                key={r.key}
                onClick={() => onPick(r.key)}
                className={cn(
                  "flex w-full flex-col items-start gap-1.5 border-b p-4 text-left text-sm leading-snug break-words whitespace-normal last:border-b-0 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                  r.key === current && "bg-sidebar-accent text-sidebar-accent-foreground",
                )}
              >
                <div className="flex w-full flex-wrap items-center gap-2">
                  <RelevanceBadge rel={r.rel} />
                  {r.edited && <span className="rounded bg-warning px-1.5 text-xs text-warning-foreground">已修改</span>}
                  {r.selected && <span className="text-xs text-primary">待下载</span>}
                </div>
                <span className="w-full font-medium">{r.title}</span>
                {r.meta && <span className="w-full text-xs text-muted-foreground">{r.meta}</span>}
                {r.reason && <span className="w-full text-xs text-muted-foreground">{r.reason}</span>}
              </button>
            ))}
            {shown.length === 0 && <p className="p-4 text-sm text-muted-foreground">{query ? "没有匹配的文献" : "暂无文献"}</p>}
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </>
  )
}
