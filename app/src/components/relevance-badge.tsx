import type { Relevance } from "@/lib/api"
import { cn } from "@/lib/utils"

// Green / amber / gray as in the charter; amber here is the 中 tier, not a warning.
const tone: Record<Relevance, string> = {
  高: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  中: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  低: "bg-muted text-muted-foreground",
  不相关: "bg-muted text-muted-foreground/80",
}

export function RelevanceBadge({ rel, className }: { rel: Relevance | null; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex min-w-9 shrink-0 justify-center rounded-full px-2 py-0.5 text-xs font-medium",
        rel ? tone[rel] : "border border-dashed text-muted-foreground",
        className,
      )}
    >
      {rel ?? "待评"}
    </span>
  )
}
