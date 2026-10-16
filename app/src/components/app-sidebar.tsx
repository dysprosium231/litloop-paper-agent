import * as React from "react"
import { BookMarkedIcon, DownloadIcon, ListFilterIcon, SettingsIcon } from "lucide-react"

import { Label } from "@/components/ui/label"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInput,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { Switch } from "@/components/ui/switch"
import { RelevanceBadge } from "@/components/relevance-badge"
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

const sections = [
  { id: "screen", title: "筛选", icon: ListFilterIcon },
  { id: "download", title: "下载", icon: DownloadIcon },
  { id: "zotero", title: "Zotero", icon: BookMarkedIcon },
  { id: "settings", title: "设置", icon: SettingsIcon },
]

export function AppSidebar({
  rows,
  current,
  onPick,
  heading,
  onlyBatch,
  onOnlyBatch,
  ...props
}: React.ComponentProps<typeof Sidebar> & {
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
    <Sidebar collapsible="icon" className="overflow-hidden *:data-[sidebar=sidebar]:flex-row" {...props}>
      <Sidebar collapsible="none" className="w-[calc(var(--sidebar-width-icon)+1px)]! border-r">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" className="md:h-8 md:p-0" tooltip={{ children: "LitLoop", hidden: false }}>
                <img src="/litloop-icon.svg" alt="" className="size-8 rounded-lg" />
                <span className="truncate font-medium">LitLoop</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent className="px-1.5 md:px-0">
              <SidebarMenu>
                {sections.map((s) => (
                  <SidebarMenuItem key={s.id}>
                    <SidebarMenuButton
                      tooltip={{ children: s.title, hidden: false }}
                      isActive={s.id === "screen"}
                      className="px-2.5 md:px-2"
                    >
                      <s.icon />
                      <span>{s.title}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>

      <Sidebar collapsible="none" className="hidden flex-1 md:flex">
        <SidebarHeader className="gap-3 border-b p-4">
          <div className="flex w-full items-center justify-between gap-2">
            <div className="truncate text-base font-medium text-foreground">{heading}</div>
            <Label className="flex shrink-0 items-center gap-2 text-sm">
              <span>只看本批</span>
              <Switch className="shadow-none" checked={onlyBatch} onCheckedChange={onOnlyBatch} />
            </Label>
          </div>
          <SidebarInput placeholder="查找标题、期刊" value={query} onChange={(e) => setQuery(e.target.value)} />
        </SidebarHeader>
        <SidebarContent>
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
                    {r.edited && <span className="rounded bg-warning px-1.5 text-xs text-warning-foreground">你改过</span>}
                    {r.selected && <span className="text-xs text-primary">已选中</span>}
                  </div>
                  <span className="w-full font-medium">{r.title}</span>
                  {r.meta && <span className="w-full text-xs text-muted-foreground">{r.meta}</span>}
                  {r.reason && <span className="w-full text-xs text-muted-foreground">{r.reason}</span>}
                </button>
              ))}
              {shown.length === 0 && <p className="p-4 text-sm text-muted-foreground">这里还没有文章。</p>}
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>
    </Sidebar>
  )
}
