import { rating, type BatchOp, type BatchPrevious, type Item, type Relevance } from "@/lib/api"

export interface ScreeningFilters {
  query: string
  rel: "all" | "unrated" | Relevance
  decision: "all" | "undecided" | "selected" | "rejected"
  yearFrom: string
  yearTo: string
  journal: string
}

export const EMPTY_FILTERS: ScreeningFilters = {
  query: "",
  rel: "all",
  decision: "all",
  yearFrom: "",
  yearTo: "",
  journal: "",
}

export function activeFilterCount(f: ScreeningFilters): number {
  let n = 0
  if (f.rel !== "all") n += 1
  if (f.decision !== "all") n += 1
  if (f.yearFrom.trim() || f.yearTo.trim()) n += 1
  if (f.journal !== "") n += 1
  return n
}

export function hasAnyFilter(f: ScreeningFilters): boolean {
  return Boolean(f.query.trim()) || activeFilterCount(f) > 0
}

function parseYear(v?: number | string): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return Math.trunc(v)
  if (typeof v === "string") {
    const m = /^\s*(\d{4})\s*$/.exec(v)
    if (m) return Number(m[1])
  }
  return null
}

function parseFilterYear(raw: string): number | null {
  const m = /^\s*(\d{4})\s*$/.exec(raw)
  return m ? Number(m[1]) : null
}

export function filterPapers<
  T extends { doi?: string; title: string; year?: number | string; journal?: string; item: Item | null },
>(papers: T[], f: ScreeningFilters): T[] {
  const q = f.query.trim().toLowerCase()
  const yFrom = parseFilterYear(f.yearFrom)
  const yTo = parseFilterYear(f.yearTo)
  return papers.filter((p) => {
    if (q) {
      const hay = [p.title, p.journal, p.year, p.doi, p.item?.reason, p.item?.reject_reason]
        .filter((x) => x !== undefined && x !== null && x !== "")
        .join(" ")
        .toLowerCase()
      if (!hay.includes(q)) return false
    }
    if (f.rel !== "all") {
      if (f.rel === "unrated") {
        if (p.item !== null) return false
      } else if (!p.item || rating(p.item) !== f.rel) {
        return false
      }
    }
    if (f.decision !== "all") {
      const dec = p.item?.decision ?? null
      if (f.decision === "undecided" && dec !== null) return false
      if (f.decision === "selected" && dec !== "selected") return false
      if (f.decision === "rejected" && dec !== "rejected") return false
    }
    if (yFrom !== null || yTo !== null) {
      const y = parseYear(p.year)
      if (y === null) return false
      if (yFrom !== null && y < yFrom) return false
      if (yTo !== null && y > yTo) return false
    }
    if (f.journal && (p.journal ?? "").trim() !== f.journal) return false
    return true
  })
}

export function collectJournals<T extends { journal?: string }>(papers: T[]): { name: string; count: number }[] {
  const counts = new Map<string, number>()
  for (const p of papers) {
    const name = (p.journal ?? "").trim()
    if (!name) continue
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
}

export function yearBounds<T extends { year?: number | string }>(papers: T[]): [number, number] | null {
  let min = Infinity
  let max = -Infinity
  for (const p of papers) {
    const y = parseYear(p.year)
    if (y === null) continue
    if (y < min) min = y
    if (y > max) max = y
  }
  return min <= max ? [min, max] : null
}

export function previousToUndoOps(previous: BatchPrevious[]): BatchOp[] {
  return previous.map((p) => {
    if (!p.existed) {
      return { doi: p.doi ?? null, title: p.title ?? null, delete_if_unrated: true }
    }
    return {
      doi: p.doi ?? null,
      title: p.title ?? null,
      ...(p.user_relevance ? { relevance: p.user_relevance } : { clear_user_relevance: true }),
      decision: p.decision ?? null,
      reject_reason: p.decision === "rejected" ? (p.reject_reason ?? null) : null,
    }
  })
}

export function itemToUndoOp(paper: { doi?: string; title: string; item: Item | null }): BatchOp {
  const it = paper.item
  if (!it) {
    return { doi: paper.doi ?? null, title: paper.title, delete_if_unrated: true }
  }
  return {
    doi: it.doi ?? paper.doi ?? null,
    title: it.title || paper.title,
    ...(it.user_relevance ? { relevance: it.user_relevance } : { clear_user_relevance: true }),
    decision: it.decision ?? null,
    reject_reason: it.decision === "rejected" ? (it.reject_reason ?? null) : null,
  }
}
