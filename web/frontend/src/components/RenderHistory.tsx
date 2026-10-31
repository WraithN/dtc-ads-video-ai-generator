import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Download, FileText,
  History, Loader2, PlayCircle, RotateCcw, Video, XCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import type { RenderRecord, ScriptHistoryRecord } from '@/lib/api'
import { cn } from '@/lib/utils'

function timeAgo(ts: number): string {
  const s = Math.max(0, Math.floor((Date.now() / 1000) - ts))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

const STATUS_STYLE: Record<RenderRecord['status'], { cls: string; Icon: typeof CheckCircle2 }> = {
  done: { cls: 'bg-emerald-500/15 text-emerald-600', Icon: CheckCircle2 },
  partial: { cls: 'bg-amber-500/15 text-amber-600', Icon: AlertTriangle },
  failed: { cls: 'bg-destructive/15 text-destructive', Icon: XCircle },
}

/**
 * Generation history over the server's SQLite store: rendered videos *and*
 * the script batches that produced them, newest first.
 *
 * A partial or failed render shows a "retry from failure point" button that
 * re-renders only the scenes that actually failed, reusing the cache for the
 * good ones — so an account top-up doesn't re-bill finished shots.
 */
export function RenderHistory({
  renders,
  scripts,
  busy,
  onResume,
}: {
  renders: RenderRecord[]
  scripts: ScriptHistoryRecord[]
  busy: boolean
  onResume: (renderId: string) => void
}) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<'videos' | 'scripts'>('videos')
  const [openScript, setOpenScript] = useState<string | null>(null)

  return (
    <Panel>
      <PanelHeader title={t('history.title')} icon={History} />
      <PanelBody className="space-y-2">
        <div className="flex gap-1 rounded-md border border-border bg-muted/40 p-0.5">
          <HistoryTab
            active={tab === 'videos'}
            onClick={() => setTab('videos')}
            Icon={Video}
            label={t('history.videoTab')}
            count={renders.length}
          />
          <HistoryTab
            active={tab === 'scripts'}
            onClick={() => setTab('scripts')}
            Icon={FileText}
            label={t('history.scriptTab')}
            count={scripts.length}
          />
        </div>

        {tab === 'videos' ? (
          renders.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">{t('history.empty')}</p>
          ) : (
            renders.map((r) => <RenderRow key={r.render_id} r={r} busy={busy} onResume={onResume} />)
          )
        ) : scripts.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">{t('history.scriptsEmpty')}</p>
        ) : (
          scripts.map((s) => (
            <ScriptRow
              key={s.script_id}
              s={s}
              open={openScript === s.script_id}
              onToggle={() => setOpenScript((v) => (v === s.script_id ? null : s.script_id))}
            />
          ))
        )}
      </PanelBody>
    </Panel>
  )
}

function HistoryTab({
  active,
  onClick,
  Icon,
  label,
  count,
}: {
  active: boolean
  onClick: () => void
  Icon: typeof Video
  label: string
  count: number
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 text-[11px] font-medium transition-colors',
        active
          ? 'bg-background text-foreground shadow-sm'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
      <span className="text-[10px] text-muted-foreground">{count}</span>
    </button>
  )
}
function RenderRow({
  r,
  busy,
  onResume,
}: {
  r: RenderRecord
  busy: boolean
  onResume: (renderId: string) => void
}) {
  const { t } = useTranslation()
  const st = STATUS_STYLE[r.status]
  const Icon = st.Icon
  const ok = r.scenes.filter((s) => s.status === 'ok').length
  const bad = r.scenes.length - ok
  const canRetry = r.status === 'partial' || r.status === 'failed'
  return (
    <div className="rounded-lg border border-border bg-background/40 p-2.5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium',
            st.cls,
          )}
        >
          <Icon className="h-3 w-3" />
          {t(`history.${r.status}`)}
        </span>
        <span className="truncate text-xs font-medium" title={r.name}>
          {r.name}
        </span>
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
          {timeAgo(r.created_at)}
        </span>
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        {r.config.resolution ? <span>{r.config.resolution}</span> : null}
        {r.total_duration ? <span>{Math.round(r.total_duration)}s</span> : null}
        {typeof r.cost === 'number' && r.cost > 0 ? <span>${r.cost.toFixed(2)}</span> : null}
        {r.scenes.length ? (
          <span>
            {ok}/{r.scenes.length} {t('history.ok')}
            {bad > 0 ? ` · ${bad} ${t('history.bad')}` : ''}
          </span>
        ) : null}
        {r.kind === 'retry' ? (
          <Badge variant="outline" className="text-[10px]">
            {t('history.retryTag')}
          </Badge>
        ) : null}
      </div>

      <div className="mt-2 flex items-center gap-2">
        {r.video_url ? (
          <>
            <a
              href={r.video_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
            >
              <PlayCircle className="h-3.5 w-3.5" />
              {t('history.view')}
            </a>
            <a
              href={r.video_url}
              download={(r.video_url.split('/').pop() || 'ad.mp4').replace(/[?#].*$/, '')}
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:underline"
            >
              <Download className="h-3.5 w-3.5" />
              {t('download.button')}
            </a>
          </>
        ) : (
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <XCircle className="h-3.5 w-3.5" />
            {t('history.noVideo')}
          </span>
        )}
        {canRetry ? (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-7 px-2 text-[11px]"
            disabled={busy}
            onClick={() => onResume(r.render_id)}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            {t('history.retry')}
          </Button>
        ) : null}
      </div>
    </div>
  )
}

/**
 * One recorded script batch: the brief that produced it plus the four
 * competing concepts. Expands in place so a past line of copy can be read
 * without leaving the render screen.
 */
function ScriptRow({
  s,
  open,
  onToggle,
}: {
  s: ScriptHistoryRecord
  open: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="rounded-lg border border-border bg-background/40 p-2.5">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 text-left"
        title={t('history.viewScript')}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="truncate text-xs font-medium" title={s.product}>
          {s.product || '—'}
        </span>
        <Badge variant="outline" className="shrink-0 text-[10px]">
          {s.language}
        </Badge>
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
          {timeAgo(s.created_at)}
        </span>
      </button>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        <span>{t('history.drafts', { n: s.drafts.length })}</span>
        {s.category ? <span>{s.category}</span> : null}
        {s.audience ? <span className="truncate">{s.audience}</span> : null}
      </div>

      {open ? (
        <div className="mt-2 space-y-2 border-t border-border pt-2">
          {s.drafts.map((d, i) => (
            <div key={`${d.type}-${i}`} className="space-y-1">
              <div className="flex items-center gap-1.5">
                <Badge
                  variant="secondary"
                  className="bg-primary/10 text-[10px] text-primary hover:bg-primary/15"
                >
                  {d.title || d.type}
                </Badge>
              </div>
              <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-foreground/90">
                {d.narration}
              </p>
              {d.rationale ? (
                <p className="text-[10px] italic text-muted-foreground">{d.rationale}</p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
