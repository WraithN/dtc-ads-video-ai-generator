import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity, AlertTriangle, Check, Circle, Clapperboard, Film, Loader2, PenLine } from 'lucide-react'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import { cn } from '@/lib/utils'
import { type Job, type JobStats } from '@/lib/api'

export type TaskKind = 'scripts' | 'plan' | 'render'

export interface ActiveTask {
  kind: TaskKind
  startedAt: number
}

/**
 * Right-hand task progress panel.
 *
 * Shows the three generation steps (scripts -> scenes -> render) with live
 * status for the running one. Generation jobs whose inner progress is not
 * knowable (one LLM call) still report a smoothed backend estimate; the
 * render job reports real scene-by-scene progress plus vendor detail
 * (submitted prompt chars, task id, poll status, received MB).
 */
export function TaskPanel({
  active,
  job,
  hasScripts,
  hasPlan,
  hasVideo,
}: {
  active: ActiveTask | null
  job: Job | null
  hasScripts: boolean
  hasPlan: boolean
  hasVideo: boolean
}) {
  const { t } = useTranslation()
  // 1s heartbeat so the elapsed counter keeps ticking between job updates.
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setTick((v) => v + 1), 1000)
    return () => window.clearInterval(id)
  }, [active])

  const steps: {
    kind: TaskKind
    label: string
    icon: typeof PenLine
    state: 'done' | 'active' | 'error' | 'idle'
  }[] = [
    {
      kind: 'scripts',
      label: t('task.stepScripts'),
      icon: PenLine,
      state: active?.kind === 'scripts' ? 'active' : hasScripts ? 'done' : 'idle',
    },
    {
      kind: 'plan',
      label: t('task.stepScenes'),
      icon: Clapperboard,
      state: active?.kind === 'plan' ? 'active' : hasPlan ? 'done' : 'idle',
    },
    {
      kind: 'render',
      label: t('task.stepRender'),
      icon: Film,
      state:
        active?.kind === 'render'
          ? job?.error
            ? 'error'
            : 'active'
          : hasVideo
            ? 'done'
            : 'idle',
    },
  ]

  const stats = job?.stats ?? undefined
  const pct = job && active ? Math.round((job.progress ?? 0) * 100) : 0
  const elapsed = elapsedSeconds(active, job, stats)

  return (
    <Panel>
      <PanelHeader title={t('task.title')} icon={Activity} />
      <PanelBody className="space-y-3">
        {steps.map((s) => {
          const Icon = s.icon
          return (
            <div key={s.kind} className="flex items-center gap-3">
              <span
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs',
                  s.state === 'done' && 'border-transparent bg-emerald-500 text-white',
                  s.state === 'active' && 'border-transparent bg-brand text-white shadow-sm',
                  s.state === 'error' && 'border-transparent bg-destructive text-white',
                  s.state === 'idle' && 'border-border text-muted-foreground',
                )}
              >
                {s.state === 'done' ? (
                  <Check className="h-3.5 w-3.5" />
                ) : s.state === 'active' ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : s.state === 'error' ? (
                  <AlertTriangle className="h-3.5 w-3.5" />
                ) : (
                  <Icon className="h-3.5 w-3.5" />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span
                    className={cn(
                      'truncate text-sm',
                      s.state === 'idle' ? 'text-muted-foreground' : 'font-medium',
                    )}
                  >
                    {s.label}
                  </span>
                  {s.state === 'active' && pct > 0 ? (
                    <span className="text-[11px] tabular-nums text-muted-foreground">{pct}%</span>
                  ) : null}
                </div>
                {s.state === 'active' ? (
                  <div className="mt-1.5">
                    {pct > 3 ? (
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out"
                          style={{ width: `${Math.min(100, Math.max(3, pct))}%` }}
                        />
                      </div>
                    ) : (
                      <IndeterminateBar />
                    )}
                    <div className="mt-1 space-y-0.5">
                      <p className="truncate text-[11px] text-muted-foreground">
                        {activeMessage(job, stats, t)}
                        {elapsed !== null ? (
                          <span className="tabular-nums"> · {t('task.elapsed', { s: elapsed })}</span>
                        ) : null}
                      </p>
                      {stats ? <StatRows stats={stats} /> : null}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          )
        })}

        {!active ? (
          <p className="flex items-center gap-1.5 pt-1 text-[11px] text-muted-foreground">
            <Circle className="h-3.5 w-3.5" />
            {t('task.idle')}
          </p>
        ) : null}

        {job?.warnings && job.warnings.length ? (
          <p className="flex items-center gap-1.5 pt-1 text-[11px] text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            {t('task.warnings', {
              n: job.warnings.length,
              skipped: job.warnings.filter((w) => w.skipped).length,
            })}
          </p>
        ) : null}
      </PanelBody>
    </Panel>
  )
}

function elapsedSeconds(active: ActiveTask | null, job: Job | null, stats?: JobStats) {
  if (!active) return null
  if (job && typeof stats?.elapsed === 'number') return stats.elapsed
  return Math.max(0, Math.round((Date.now() - active.startedAt) / 1000))
}

/** Progress line: prefer the backend phase key (translatable), else raw message. */
function activeMessage(job: Job | null, stats: JobStats | undefined, t: ReturnType<typeof useTranslation>['t']) {
  if (!job) return t('task.starting')
  const phase = stats?.phase
  if (phase) {
    const key = `task.phase.${phase}`
    const label = t(key, { defaultValue: '' })
    if (label) return label
  }
  return job.message || t('task.working')
}

function StatRows({ stats }: { stats: JobStats }) {
  const { t } = useTranslation()
  const rows: string[] = []
  if (typeof stats.scene_index === 'number' && typeof stats.scene_total === 'number') {
    rows.push(t('task.stats.scene', { i: stats.scene_index, n: stats.scene_total }))
  }
  if (typeof stats.prompt_chars === 'number') {
    rows.push(t('task.stats.promptChars', { n: stats.prompt_chars }))
  }
  if (stats.task_id) {
    rows.push(t('task.stats.taskId', { id: String(stats.task_id).slice(-8) }))
  }
  if (stats.status && typeof stats.status === 'string' && !['success', 'succeeded'].includes(stats.status)) {
    rows.push(t('task.stats.status', { status: stats.status }))
  }
  if (typeof stats.received_mb === 'number' && stats.received_mb > 0) {
    rows.push(t('task.stats.received', { mb: stats.received_mb }))
  }
  if (stats.cached) rows.push(t('task.stats.cached'))
  if (!rows.length) return null
  return (
    <div className="flex flex-wrap gap-x-3">
      {rows.map((r, i) => (
        <span key={i} className="rounded bg-muted/70 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {r}
        </span>
      ))}
    </div>
  )
}

/** Slim animated bar for when no believable percentage exists yet. */
function IndeterminateBar() {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className="indeterminate-bar h-full w-2/5 rounded-full bg-brand/80" />
    </div>
  )
}
