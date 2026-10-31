import { useTranslation } from 'react-i18next'
import { Check, FileText, ClipboardCheck, Clapperboard, Wand2, Download } from 'lucide-react'
import { cn } from '@/lib/utils'

export type StepId = 'script' | 'confirm' | 'render' | 'refine' | 'download'

const ORDER: StepId[] = ['script', 'confirm', 'render', 'refine', 'download']

const ICONS: Record<StepId, React.ComponentType<{ className?: string }>> = {
  script: FileText,
  confirm: ClipboardCheck,
  render: Clapperboard,
  refine: Wand2,
  download: Download,
}

/**
 * The five-step flow bar.
 *
 * Completion is reported from the app's real state (scripts exist, storyboard
 * exists, video rendered, file exported) — never from "where the user
 * clicked". Clicking a node only switches the view; it must never turn a step
 * into a tick, because a tick means "this artefact exists" and an artefact
 * does not appear because someone looked at it.
 */
export function Stepper({
  current,
  done,
  unlocked,
  onSelect,
}: {
  current: StepId
  /** Per-step completion, driven by real artefacts. */
  done: Record<StepId, boolean>
  /** May the user jump to this step yet? */
  unlocked: (s: StepId) => boolean
  onSelect: (s: StepId) => void
}) {
  const { t } = useTranslation()

  return (
    <ol className="flex w-full items-stretch gap-1 overflow-x-auto">
      {ORDER.map((s, i) => {
        const isDone = Boolean(done[s])
        const active = s === current
        const can = unlocked(s)
        const Icon = ICONS[s]
        return (
          <li key={s} className="flex min-w-0 flex-1 items-center">
            <button
              type="button"
              disabled={!can}
              onClick={() => can && onSelect(s)}
              className={cn(
                'group flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-all',
                active && 'bg-brand-soft',
                can ? 'cursor-pointer hover:bg-accent/60' : 'cursor-not-allowed opacity-50',
              )}
            >
              <span
                className={cn(
                  'flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-all',
                  isDone && !active && 'border-transparent bg-primary text-primary-foreground',
                  active &&
                    'border-transparent bg-brand text-white shadow-md ring-4 ring-brand/25',
                  !isDone && !active && 'border-muted-foreground/30 text-muted-foreground',
                )}
              >
                {isDone ? <Check className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
              </span>
              <span className="min-w-0">
                <span
                  className={cn(
                    'block truncate text-xs font-medium leading-tight',
                    active ? 'text-foreground' : 'text-muted-foreground',
                  )}
                >
                  {t(`steps.${s}`)}
                </span>
                <span className="block text-[10px] font-medium uppercase tracking-wide text-muted-foreground/90">
                  {String(i + 1).padStart(2, '0')}
                </span>
              </span>
            </button>
            {i < ORDER.length - 1 ? (
              <span
                className={cn(
                  'h-0.5 w-5 shrink-0 rounded-full transition-colors',
                  isDone ? 'bg-primary' : 'bg-border',
                )}
              />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}
