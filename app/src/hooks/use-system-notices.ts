import * as React from "react"

import { api, downloadsApi, type DownloadNotice, type RoundNotice } from "@/lib/api"

/**
 * Windows notifications, on every page.
 *
 * Downloads: once for each paper that waits for the user's login or human
 * check (a paper that asks again later is not repeated), and once when a list
 * finishes. Covers the app's own jobs and those the agent or the command line
 * runs while the app is open.
 *
 * Deep search: once when a round finishes, so a batch the agent is building in
 * the background does not go unnoticed.
 */
export function useSystemNotices() {
  const seen = React.useRef(new Set<string>())
  const running = React.useRef<string | null>(null)  // started_at of the download seen running
  const rounds = React.useRef(new Map<string, number>())  // "session|started" of the round seen running
  React.useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return  // browser preview: nothing to notify with
    let stopped = false
    const poll = async () => {
      let n: DownloadNotice | null
      try {
        n = await downloadsApi.notice()
      } catch {
        return
      }
      if (stopped || !n?.started_at) return
      const run = n.started_at
      if (n.status === "running") {
        running.current = run
        for (const [key, a] of Object.entries(n.attention ?? {})) {
          const id = `${run}|${key}`
          if (seen.current.has(id)) continue
          seen.current.add(id)
          notify("LitLoop 需要你操作", a.message)
        }
      } else if (n.status === "done" && running.current === run) {
        running.current = null
        const all = n.done >= n.total
        notify(all ? "下载完成" : "下载已停止",
          `${n.task || "文献下载"}：成功 ${n.success} 篇` + (n.failed ? `，失败 ${n.failed} 篇` : "") + (all ? "" : `，未处理 ${n.total - n.done} 篇`))
      }
    }
    const pollRounds = async () => {
      let n: RoundNotice | null
      try {
        n = await api.roundNotice()
      } catch {
        return
      }
      if (stopped || !n?.started) return
      const id = `${n.session}|${n.started}`
      if (n.state === "running") {
        rounds.current.set(id, 1)
        return
      }
      if (!rounds.current.has(id) || seen.current.has(id)) return
      rounds.current.delete(id)
      seen.current.add(id)
      notify("深度检索有新的一批",
        n.state === "failed" ? `${n.question || "深度检索"}：这一轮失败了`
          : `${n.question || "深度检索"}：第 ${Math.max(1, n.batches)} 批已就绪`)
    }
    poll()
    pollRounds()
    const t = setInterval(poll, 3000)
    const r = setInterval(pollRounds, 3000)
    return () => { stopped = true; clearInterval(t); clearInterval(r) }
  }, [])
}

let allowed: Promise<boolean> | null = null

async function notify(title: string, body: string) {
  try {
    const { isPermissionGranted, requestPermission, sendNotification } = await import("@tauri-apps/plugin-notification")
    allowed ??= isPermissionGranted().then(async (ok) => ok || (await requestPermission()) === "granted")
    if (await allowed) sendNotification({ title, body })
  } catch {
    // An app built without the notification plugin: the notice on the download page still shows.
  }
}
