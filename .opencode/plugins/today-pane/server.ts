import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"
import { Plugin } from "@opencode/plugin"

// Same trigger as the Claude Code SessionStart hook: open the today.md pane for this project.
function openTodayPane(directory: string) {
  const script = join(directory, "scripts/today_pane.sh")
  if (!existsSync(script)) return
  spawn("bash", [script], { cwd: directory, detached: true, stdio: "ignore" }).unref()
}

export default Plugin.define({
  id: "diet-log.today-pane",
  setup(ctx) {
    const directory = ctx.location.directory
    openTodayPane(directory)

    const controller = new AbortController()
    void (async () => {
      try {
        for await (const _ of ctx.event.subscribe({ types: ["session.created"], signal: controller.signal })) {
          openTodayPane(directory)
        }
      } catch (err) {
        if (!controller.signal.aborted) console.error("[today-pane]", err)
      }
    })()

    return () => controller.abort()
  },
})
