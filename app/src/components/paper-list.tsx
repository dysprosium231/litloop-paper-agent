import * as React from "react"
import { ChevronDownIcon, SlidersHorizontalIcon } from "lucide-react"

import { RelevanceBadge } from "@/components/relevance-badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { SidebarContent, SidebarGroup, SidebarGroupContent, SidebarHeader, SidebarInput } from "@/components/ui/sidebar"
import { Switch } from "@/components/ui/switch"
import { RELEVANCE, REJECT_REASONS, type Item, type Relevance } from "@/lib/api"
import {
  EMPTY_FILTERS,
  activeFilterCount,
  hasAnyFilter,
  type ScreeningFilters,
} from "@/lib/screening-filters"
import { cn } from "@/lib/utils"

export interface PaperRow {
  key: string
  title: string
  meta: string
  reason: string
  rel: Relevance | null // null: not rated yet
  edited: boolean
  selected: boolean
  rejected: boolean
  rejectReason: string
  hasItem: boolean
}

export type QuickBatchKind =
  | "accept_high"
  | "accept_high_med"
  | "reject_irrelevant"
  | "reject_low_irrelevant"

const REL_FILTERS: { id: ScreeningFilters["rel"]; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "高", label: "高" },
  { id: "中", label: "中" },
  { id: "低", label: "低" },
  { id: "不相关", label: "不相关" },
  { id: "unrated", label: "待评" },
]

const DECISION_FILTERS: { id: ScreeningFilters["decision"]; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "undecided", label: "待处理" },
  { id: "selected", label: "待下载" },
  { id: "rejected", label: "已排除" },
]

/** The second column on the screening section: filters, batch actions, and paper rows. */
export function PaperList({
  rows,
  totalCount,
  current,
  onPick,
  heading,
  onlyBatch,
  onOnlyBatch,
  filters,
  onFilters,
  journals,
  yearRange,
  checked,
  onToggleCheck,
  onCheckAll,
  onClearCheck,
  quickCounts,
  busy,
  onQuickAction,
  onBatchDecision,
  onBatchRate,
}: {
  rows: PaperRow[]
  totalCount: number
  current: string | null
  onPick: (key: string) => void
  heading: string
  onlyBatch: boolean
  onOnlyBatch: (v: boolean) => void
  filters: ScreeningFilters
  onFilters: (next: ScreeningFilters) => void
  journals: { name: string; count: number }[]
  yearRange: [number, number] | null
  checked: Set<string>
  onToggleCheck: (key: string, shiftKey: boolean) => void
  onCheckAll: () => void
  onClearCheck: () => void
  quickCounts: { high: number; highMed: number; irrelevant: number; lowIrrelevant: number }
  busy: boolean
  onQuickAction: (kind: QuickBatchKind) => void
  onBatchDecision: (decision: Item["decision"], reason?: string) => void
  onBatchRate: (rel: Relevance | null) => void
}) {
  const [filterOpen, setFilterOpen] = React.useState(false)
  const filterCount = activeFilterCount(filters)
  const filtering = hasAnyFilter(filters)
  const allVisibleChecked = rows.length > 0 && rows.every((r) => checked.has(r.key))

  const updateYear = (field: "yearFrom" | "yearTo", raw: string) => {
    const digits = raw.replace(/\D/g, "").slice(0, 4)
    onFilters({ ...filters, [field]: digits })
  }

  return (
    <>
      <SidebarHeader className="gap-2.5 border-b p-3.5">
        <div className="flex w-full items-center justify-between gap-2">
          <div className="min-w-0 truncate text-base font-medium text-foreground">{heading}</div>
          <Label className="flex shrink-0 items-center gap-2 text-sm">
            <span>只看本批</span>
            <Switch className="shadow-none" checked={onlyBatch} onCheckedChange={onOnlyBatch} />
          </Label>
        </div>

        <div className="flex items-center gap-1.5">
          <SidebarInput
            placeholder="搜索标题、期刊或 Agent 理由"
            value={filters.query}
            onChange={(e) => onFilters({ ...filters, query: e.target.value })}
            className="min-w-0 flex-1"
          />
          <Button
            type="button"
            size="sm"
            variant={filterOpen || filterCount > 0 ? "secondary" : "outline"}
            onClick={() => setFilterOpen((o) => !o)}
            aria-label="筛选条件"
            aria-expanded={filterOpen}
            className="shrink-0 gap-1 px-2.5"
          >
            <SlidersHorizontalIcon className="size-3.5" />
            {filterCount > 0 && <span className="text-xs tabular-nums">{filterCount}</span>}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" size="sm" variant="outline" disabled={busy} className="shrink-0 gap-1 px-2.5">
                批量
                <ChevronDownIcon className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-60">
              <DropdownMenuItem disabled={quickCounts.high === 0} onSelect={() => onQuickAction("accept_high")}>
                将未决定的「高」加入下载（{quickCounts.high} 篇）
              </DropdownMenuItem>
              <DropdownMenuItem disabled={quickCounts.highMed === 0} onSelect={() => onQuickAction("accept_high_med")}>
                将未决定的「高、中」加入下载（{quickCounts.highMed} 篇）
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem disabled={quickCounts.irrelevant === 0} onSelect={() => onQuickAction("reject_irrelevant")}>
                排除未决定的「不相关」（{quickCounts.irrelevant} 篇）
              </DropdownMenuItem>
              <DropdownMenuItem disabled={quickCounts.lowIrrelevant === 0} onSelect={() => onQuickAction("reject_low_irrelevant")}>
                排除未决定的「低、不相关」（{quickCounts.lowIrrelevant} 篇）
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem disabled={rows.length === 0} onSelect={onCheckAll}>
                全选当前列表（{rows.length} 篇）
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {filterOpen && (
          <div className="space-y-2.5 rounded-lg border bg-background p-2.5 text-xs">
            <div className="space-y-1">
              <div className="text-muted-foreground">相关度</div>
              <div className="flex flex-wrap gap-1">
                {REL_FILTERS.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => onFilters({ ...filters, rel: opt.id })}
                    className={cn(
                      "rounded-md border px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground",
                      filters.rel === opt.id && "border-primary bg-primary/10 font-medium text-primary",
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1">
              <div className="text-muted-foreground">决定状态</div>
              <div className="flex flex-wrap gap-1">
                {DECISION_FILTERS.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => onFilters({ ...filters, decision: opt.id })}
                    className={cn(
                      "rounded-md border px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground",
                      filters.decision === opt.id && "border-primary bg-primary/10 font-medium text-primary",
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <div className="text-muted-foreground">年份范围</div>
                <div className="flex items-center gap-1">
                  <input
                    type="text"
                    inputMode="numeric"
                    placeholder={yearRange ? String(yearRange[0]) : "起始年"}
                    value={filters.yearFrom}
                    onChange={(e) => updateYear("yearFrom", e.target.value)}
                    aria-label="起始年份"
                    className="h-7 w-full min-w-0 rounded-md border bg-background px-2 text-xs outline-none focus:border-primary"
                  />
                  <span className="text-muted-foreground">–</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    placeholder={yearRange ? String(yearRange[1]) : "结束年"}
                    value={filters.yearTo}
                    onChange={(e) => updateYear("yearTo", e.target.value)}
                    aria-label="结束年份"
                    className="h-7 w-full min-w-0 rounded-md border bg-background px-2 text-xs outline-none focus:border-primary"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <div className="text-muted-foreground">期刊</div>
                <select
                  value={filters.journal}
                  onChange={(e) => onFilters({ ...filters, journal: e.target.value })}
                  aria-label="按期刊筛选"
                  className="h-7 w-full min-w-0 rounded-md border bg-background px-2 text-xs outline-none focus:border-primary"
                >
                  <option value="">全部期刊</option>
                  {journals.map((j) => (
                    <option key={j.name} value={j.name}>
                      {j.name}（{j.count}）
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        )}

        {filtering && (
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span className="tabular-nums">显示 {rows.length} / {totalCount} 篇</span>
            <button
              type="button"
              onClick={() => onFilters(EMPTY_FILTERS)}
              className="text-primary hover:underline"
            >
              重置筛选
            </button>
          </div>
        )}
      </SidebarHeader>

      {checked.size > 0 && (
        <div className="space-y-2 border-b bg-accent/40 px-3.5 py-2.5 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-foreground tabular-nums">已选 {checked.size} 篇</span>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={allVisibleChecked ? onClearCheck : onCheckAll}
                className="text-primary hover:underline"
              >
                {allVisibleChecked ? "取消全选" : "全选当前"}
              </button>
              <button type="button" onClick={onClearCheck} className="text-muted-foreground hover:text-foreground">
                清空
              </button>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              type="button"
              size="sm"
              disabled={busy}
              onClick={() => onBatchDecision("selected")}
              className="h-7 px-2.5 text-xs"
            >
              加入下载
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" size="sm" variant="outline" disabled={busy} className="h-7 gap-1 px-2.5 text-xs">
                  排除
                  <ChevronDownIcon className="size-3" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-44">
                {REJECT_REASONS.map((r) => (
                  <DropdownMenuItem key={r} onSelect={() => onBatchDecision("rejected", r)}>
                    {r}
                  </DropdownMenuItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => onBatchDecision("rejected")}>不写理由</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" size="sm" variant="outline" disabled={busy} className="h-7 gap-1 px-2.5 text-xs">
                  更多
                  <ChevronDownIcon className="size-3" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-44">
                {RELEVANCE.map((r) => (
                  <DropdownMenuItem key={r} onSelect={() => onBatchRate(r)}>
                    评分设为「{r}」
                  </DropdownMenuItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => onBatchRate(null)}>恢复 Agent 评分</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => onBatchDecision(null)}>清除决定</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      )}

      <SidebarContent className="scrollable">
        <SidebarGroup className="px-0">
          <SidebarGroupContent>
            {rows.map((r) => (
              <div
                key={r.key}
                className={cn(
                  "flex w-full items-start gap-2.5 border-b px-3.5 py-3 text-left text-sm leading-snug last:border-b-0 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                  r.key === current && "bg-sidebar-accent text-sidebar-accent-foreground",
                )}
              >
                <input
                  type="checkbox"
                  checked={checked.has(r.key)}
                  onChange={() => {}}
                  onClick={(e) => {
                    e.stopPropagation()
                    onToggleCheck(r.key, e.shiftKey)
                  }}
                  aria-label={`选择 ${r.title}`}
                  className="mt-1 size-4 shrink-0 cursor-pointer accent-primary"
                />
                <button
                  type="button"
                  onClick={() => onPick(r.key)}
                  className="flex min-w-0 flex-1 flex-col items-start gap-1.5 text-left break-words whitespace-normal"
                >
                  <div className="flex w-full flex-wrap items-center gap-2">
                    <RelevanceBadge rel={r.rel} />
                    {r.edited && <span className="rounded bg-warning px-1.5 text-xs text-warning-foreground">已修改</span>}
                    {r.selected && <span className="text-xs text-primary">待下载</span>}
                    {r.rejected && (
                      <span className="rounded bg-muted px-1.5 text-xs text-muted-foreground">已排除</span>
                    )}
                  </div>
                  <span className={cn("w-full font-medium", r.rejected && "line-through opacity-70")}>{r.title}</span>
                  {r.meta && <span className="w-full text-xs text-muted-foreground">{r.meta}</span>}
                  {r.reason && <span className="w-full text-xs text-muted-foreground">{r.reason}</span>}
                  {r.rejected && r.rejectReason && (
                    <span className="w-full text-xs text-muted-foreground">排除理由：{r.rejectReason}</span>
                  )}
                </button>
              </div>
            ))}
            {rows.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">{filtering ? "没有匹配的文献" : "暂无文献"}</p>
            )}
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </>
  )
}
