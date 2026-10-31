import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Job } from '@/lib/api'

/**
 * Tracks one render job over WebSocket, with polling as a fallback.
 *
 * The pipeline runs in a worker thread and broadcasts on the WS channel; if
 * the socket fails (proxy, restart) we degrade to polling /api/jobs/{id}
 * rather than losing the job silently.
 */
export function useJob(onLog?: (line: string) => void) {
  const [job, setJob] = useState<Job | null>(null)
  const [running, setRunning] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<number | null>(null)
  const logRef = useRef(onLog)
  logRef.current = onLog
  // De-duplicate log lines: the pipeline re-broadcasts the same job state
  // over both the websocket and the polling fallback, and a single scene can
  // spend many seconds on one phase. Logging every push would print
  // "Scene 4/11 done" a dozen times. Only emit when the (stage, message)
  // pair actually changes.
  const lastLogRef = useRef<string | null>(null)

  const stop = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => stop, [stop])

  const watch = useCallback(
    (jobId: string) => {
      stop()
      setRunning(true)
      setJob(null)
      lastLogRef.current = null

      const maybeLog = (j: Job) => {
        const key = `${j.stage}|${j.message}`
        if (lastLogRef.current === key) return
        lastLogRef.current = key
        logRef.current?.(`[${j.stage}] ${j.message}`)
      }

      const finish = (j: Job) => {
        setJob(j)
        if (j.done) {
          setRunning(false)
          stop()
        }
      }

      const poll = () => {
        if (timerRef.current) return
        timerRef.current = window.setInterval(async () => {
          try {
            const j = await api.getJob(jobId)
            maybeLog(j)
            finish(j)
          } catch {
            /* transient; keep polling */
          }
        }, 1500)
      }

      try {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws'
        const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`)
        wsRef.current = ws
        ws.onmessage = (ev) => {
          const d = JSON.parse(ev.data) as Job
          maybeLog(d)
          finish(d)
        }
        ws.onerror = () => poll()
        ws.onclose = () => {
          if (running) poll()
        }
      } catch {
        poll()
      }
    },
    [stop, running],
  )

  return { job, running, watch, stop }
}
