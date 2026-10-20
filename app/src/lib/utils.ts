export { cn } from "cn"

/** A duration written as docs/UI_COPY.md 4.10 asks: seconds under a minute, minutes and seconds under an hour, else hours and minutes. */
export function duration(seconds: number): string {
  const s = Math.max(1, Math.round(seconds))
  if (s < 60) return `${s} 秒`
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), rem = s % 60
  if (h > 0) return m > 0 ? `${h} 小时 ${m} 分` : `${h} 小时`
  return rem > 0 ? `${m} 分 ${rem} 秒` : `${m} 分`
}
