import * as React from "react"

import { downloadsApi, type DownloadNotice } from "@/lib/api"

/**
 * Windows notifications for downloads, on every page: once for each paper that
 * waits for the user's login or human check (a paper that asks again later is
 * not repeated), and once when a list finishes. Covers the app's own jobs and
 * those the agent or the command line runs while the app is open.
 */
export function useSystemNotices() {
  const seen = React.useRef(new Set<string>())
  const running = React.useRef<string | null>(null)  // started_at of the download seen running
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
    poll()
    const t = setInterval(poll, 3000)
    return () => { stopped = true; clearInterval(t) }
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
