import * as React from "react"

import { cn } from "@/lib/utils"

/*
 * Page frame shared by the sections (downloads, Zotero, settings): a 60px white
 * header with the title (and tabs or actions), a gray canvas below with the
 * content in white cards, centered. Sizes: title 20px semibold, section titles
 * 15px semibold, body 14px, notes 12px; card padding 20px, gaps 24px.
 */

export function PageHeader({ title, tabs, active, onTab, children }: {
  title: React.ReactNode
  tabs?: { id: string; label: string; count?: number }[]
  active?: string
  onTab?: (id: string) => void
  children?: React.ReactNode
}) {
  return (
    <header className="flex h-15 shrink-0 items-stretch gap-6 border-b bg-background px-8">
      <h1 className="flex min-w-0 items-center text-xl font-semibold">{title}</h1>
      {tabs && (
        <>
          <div className="my-5 w-px bg-border" />
          <div role="tablist" className="flex gap-7">
            {tabs.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={active === t.id} onClick={() => onTab?.(t.id)}
                className={cn("-mb-px flex items-center gap-1.5 border-b-2 text-[15px] text-muted-foreground hover:text-foreground",
                  active === t.id ? "border-primary font-medium text-foreground" : "border-transparent")}>
                {t.label}
                {!!t.count && <span className="rounded-full bg-muted px-1.5 text-xs text-muted-foreground tabular-nums">{t.count}</span>}
              </button>
            ))}
          </div>
        </>
      )}
      {children && <div className="ml-auto flex shrink-0 items-center gap-2">{children}</div>}
    </header>
  )
}

/** A strip under the header for filters and status (white, like the header). */
export function PageToolbar({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-h-13 shrink-0 flex-wrap items-center gap-x-5 gap-y-2 border-b bg-background px-8 py-2.5", className)}>
      {children}
    </div>
  )
}

/** The scrolling gray canvas; content is centered at a readable width. */
export function PageBody({ children, wide, className }: { children: React.ReactNode; wide?: boolean; className?: string }) {
  return (
    <div className="scrollable min-h-0 flex-1 overflow-y-auto bg-canvas">
      <div className={cn("mx-auto w-full space-y-6 px-8 py-8", wide ? "max-w-5xl" : "max-w-3xl", className)}>{children}</div>
    </div>
  )
}

export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("rounded-xl border bg-card text-card-foreground", className)} {...props} />
}

/** Title of a block on the canvas, with an optional note and actions on the right. */
export function SectionTitle({ title, note, children }: { title: React.ReactNode; note?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-4">
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {note && <div className="mt-0.5 text-xs text-muted-foreground">{note}</div>}
      </div>
      {children && <div className="flex shrink-0 items-center gap-2">{children}</div>}
    </div>
  )
}

/** Empty state inside a card. */
export function Empty({ title, note }: { title: string; note?: string }) {
  return (
    <div className="px-6 py-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}
