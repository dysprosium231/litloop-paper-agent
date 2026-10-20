import * as React from "react"
import { ChevronDownIcon, ChevronRightIcon, FolderIcon, SearchIcon } from "lucide-react"

import type { ZoteroCollection } from "@/lib/api"
import { cn } from "@/lib/utils"

const byName = (a: ZoteroCollection, b: ZoteroCollection) =>
  a.name.localeCompare(b.name, "zh-CN", { numeric: true, sensitivity: "base" })

/**
 * Pick a Zotero collection from its tree, as Zotero shows it: children under their
 * parent, collapsed until opened. The panel opens right under the button (no
 * positioning library, no animation) and a search box narrows the tree.
 */
export function CollectionPicker({ collections, value, onChange, disabled, placeholder }: {
  collections: ZoteroCollection[] | null
  value: string
  onChange: (key: string) => void
  disabled?: boolean
  placeholder: string
}) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set())
  const root = React.useRef<HTMLDivElement>(null)

  const children = React.useMemo(() => {
    const m = new Map<string | null, ZoteroCollection[]>()
    for (const c of collections ?? []) {
      const p = c.parent && collections?.some((x) => x.key === c.parent) ? c.parent : null
      m.set(p, [...(m.get(p) ?? []), c])
    }
    for (const list of m.values()) list.sort(byName)
    return m
  }, [collections])

  // While searching: the matches and their ancestors, all open.
  const visible = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q || !collections) return null
    const byKey = new Map(collections.map((c) => [c.key, c]))
    const keep = new Set<string>()
    for (const c of collections) {
      if (!c.name.toLowerCase().includes(q)) continue
      for (let k: string | null = c.key; k && !keep.has(k); k = byKey.get(k)?.parent ?? null) keep.add(k)
    }
    return keep
  }, [query, collections])

  React.useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", close)
    document.addEventListener("keydown", esc)
    return () => {
      document.removeEventListener("mousedown", close)
      document.removeEventListener("keydown", esc)
    }
  }, [open])

  // Opening shows the chosen collection: its ancestors start expanded.
  const show = () => {
    const byKey = new Map((collections ?? []).map((c) => [c.key, c]))
    const up = new Set(expanded)
    for (let k = byKey.get(value)?.parent ?? null; k; k = byKey.get(k)?.parent ?? null) up.add(k)
    setExpanded(up)
    setQuery("")
    setOpen(true)
  }

  const toggle = (k: string) => setExpanded((s) => {
    const n = new Set(s)
    if (n.has(k)) n.delete(k)
    else n.add(k)
    return n
  })

  const rows = (parent: string | null, depth: number): React.ReactNode[] =>
    (children.get(parent) ?? []).filter((c) => !visible || visible.has(c.key)).flatMap((c) => {
      const kids = (children.get(c.key) ?? []).length > 0
      const isOpen = visible ? true : expanded.has(c.key)
      return [
        <div key={c.key} role="treeitem" aria-selected={c.key === value} aria-expanded={kids ? isOpen : undefined}
          className={cn("flex items-center rounded-sm pr-2 text-sm hover:bg-accent", c.key === value && "bg-accent font-medium")}
          style={{ paddingLeft: depth * 16 + 4 }}>
          <button type="button" tabIndex={-1} aria-label={isOpen ? "收起" : "展开"}
            className={cn("flex size-6 shrink-0 items-center justify-center text-muted-foreground", !kids && "invisible")}
            onClick={() => toggle(c.key)}>
            {isOpen ? <ChevronDownIcon className="size-3.5" /> : <ChevronRightIcon className="size-3.5" />}
          </button>
          <button type="button" className="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 text-left"
            onClick={() => { onChange(c.key); setOpen(false) }}>
            <FolderIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{c.name}</span>
          </button>
        </div>,
        ...(kids && isOpen ? rows(c.key, depth + 1) : []),
      ]
    })

  const chosen = collections?.find((c) => c.key === value)
  return (
    <div ref={root} className="relative min-w-0 flex-1">
      <button type="button" disabled={disabled} onClick={() => (open ? setOpen(false) : show())}
        aria-haspopup="tree" aria-expanded={open} aria-label="分类"
        className="flex h-9 w-full items-center gap-2 rounded-md border bg-transparent px-3 text-left text-sm shadow-xs outline-none focus-visible:border-foreground/30 disabled:cursor-not-allowed disabled:opacity-50">
        <span className={cn("min-w-0 flex-1 truncate", !chosen && "text-muted-foreground")} title={chosen?.path}>
          {chosen?.path ?? placeholder}
        </span>
        <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
      </button>
      {open && (
        <div className="absolute top-full right-0 left-0 z-50 mt-1 rounded-md border bg-popover text-popover-foreground shadow-md">
          <div className="flex items-center gap-2 border-b px-3">
            <SearchIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索分类"
              className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
          </div>
          <div role="tree" className="scrollable max-h-80 overflow-y-auto p-1">
            {rows(null, 0)}
            {visible?.size === 0 && <p className="px-3 py-2 text-xs text-muted-foreground">没有匹配的分类</p>}
          </div>
        </div>
      )}
    </div>
  )
}
