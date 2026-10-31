import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AlertTriangle, Check, CheckCircle2, ChevronDown, ChevronRight, Clock, Copy, Download,
  Film, FileText, Filter, History, Loader2, PlayCircle, RefreshCw, RotateCcw, Search,
  Video, Wallet, X, XCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import type { RenderRecord, ScriptHistoryRecord } from '@/lib/api'
import { cn } from '@/lib/utils'

/** `created_at` is a Unix timestamp from the server (not Date.now()). */
function timeAgo(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

function stamp(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString()
  } catch {
    return ''
  }
}

const STATUS_STYLE: Record<RenderRecord['status'], { cls: string; Icon: typeof CheckCircle2 }> = {
  done: { cls: 'bg-emerald-500/15 text-emerald-600', Icon: CheckCircle2 },
  partial: { cls: 'bg-amber-500/15 text-amber-600', Icon: AlertTriangle },
  failed: { cls: 'bg-destructive/15 text-destructive', Icon: XCircle },
}

type Tab = 'videos' | 'scripts'
type StringStatus = RenderRecord['status'] | 'all'

/**
 * Full-page history over the server's SQLite store: every render (including
 * resumed retries) and every script batch that was generated, with search,
 * status filtering and — for failed runs — retry from the failure point.
 *
 * Nothing here regenerates anything on its own: the "retry" button only ever
 * re-runs the scenes that failed, reusing the content-addressed cache for the
 * ones that already succeeded, so spent fees are never paid twice.
 */
export function HistoryPage({
  renders,
  scripts,
  loading,
  busy,
  onResume,
  onRefresh,
}: {
  renders: RenderRecord[]
  scripts: ScriptHistoryRecord[]
  loading: boolean
  busy: boolean
  onResume: (renderId: string) => void
  onRefresh: () => void
}) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('videos')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<StringStatus>('all')
  const [openScripts, setOpenScripts] = useState<Record<string, boolean>>({})

  // ---- derived stats ---------------------------------------------------
  const stats = useMemo(() => {
    const okVideos = renders.filter((r) => r.status === 'done').length
    const cost = renders.reduce((n, r) => n + (r.cost || 0), 0)
    const draftCount = scripts.reduce((n, s) => n + s.drafts.length, 0)
    return { videos: renders.length, okVideos, cost, batches: scripts.length, draftCount }
  }, [renders, scripts])

  // ---- filtering -------------------------------------------------------
  const needle = q.trim().toLowerCase()

  const videos = useMemo(() => {
    return renders.filter((r) => {
      if (status !== 'all' && r.status !== status) return false
      if (!needle) return true
      return [r.name, r.render_id, r.config.resolution, r.config.video_provider ?? '']
        .join(' ')
        .toLowerCase()
        .includes(needle)
    })
  }, [renders, status, needle])

  const batches = useMemo(() => {
    return scripts.filter((s) => {
      if (status !== 'all') return true // scripts have no render status
      if (!needle) return true
      return [
        s.product,
        s.category ?? '',
        s.audience ?? '',
        s.tone ?? '',
        s.language,
        ...s.drafts.map((d) => `${d.title} ${d.narration} ${d.rationale}`),
      ]
        .join(' ')
        .toLowerCase()
        .includes(needle)
    })
  }, [scripts, status, needle])

  return (
    <div className="space-y-5">
      {/* ------------------------------------------------------ summary */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard icon={Video} label={t('historyPage.statVideos')} value={String(stats.okVideos)} sub={`${stats.videos} ${t('historyPage.statRuns')}`} />
        <StatCard icon={Wallet} label={t('historyPage.statCost')} value={`$${stats.cost.toFixed(2)}`} sub={t('historyPage.statCostSub')} />
        <StatCard icon={FileText} label={t('historyPage.statScripts')} value={String(stats.draftCount)} sub={`${stats.batches} ${t('historyPage.statBatches')}`} />
        <StatCard icon={Film} label={t('historyPage.statScenes')} value={String(renders.reduce((n, r) => n + r.scenes.length, 0))} sub={t('historyPage.statScenesSub')} />
      </div>

      <Panel>
        <PanelHeader
          title={t('historyPage.title')}
          icon={History}
          subtitle={t('historyPage.subtitle')}
          action={
            <Button variant="ghost" size="sm" onClick={onRefresh} disabled={loading}>
              <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
              {t('historyPage.refresh')}
            </Button>
          }
        />
        <PanelBody className="space-y-4">
          {/* ---------------------------------------------------- controls */}
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="flex gap-1 rounded-lg border border-border bg-muted/40 p-0.5">
              <PageTab active={tab === 'videos'} onClick={() => setTab('videos')} Icon={Video} label={t('history.videoTab')} count={videos.length} />
              <PageTab active={tab === 'scripts'} onClick={() => setTab('scripts')} Icon={FileText} label={t('history.scriptTab')} count={batches.length} />
            </div>

            <div className="relative sm:ml-2 sm:w-72">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={t('historyPage.searchPlaceholder')}
                className="h-8 w-full rounded-md border border-input bg-background pl-8 pr-7 text-xs outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
              />
              {q ? (
                <button type="button" onClick={() => setQ('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>

            {tab === 'videos' ? (
              <div className="flex items-center gap-1 sm:ml-auto">
                <Filter className="h-3.5 w-3.5 text-muted-foreground" />
                {(['all', 'done', 'partial', 'failed'] as StringStatus[]).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setStatus(s)}
                    className={cn(
                      'rounded-full px-2.5 py-1 text-[11px] transition-colors',
                      status === s
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                    )}
                  >
                    {s === 'all' ? t('historyPage.all') : t(`history.${s}`)}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {/* ------------------------------------------------------- lists */}
          {loading && renders.length === 0 && scripts.length === 0 ? (
            <div className="flex items-center justify-center gap-2 py-10 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('historyPage.loading')}
            </div>
          ) : tab === 'videos' ? (
            videos.length === 0 ? (
              <EmptyState text={needle || status !== 'all' ? t('historyPage.noMatch') : t('history.empty')} />
            ) : (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {videos.map((r) => (
                  <VideoCard key={r.render_id} r={r} busy={busy} onResume={onResume} />
                ))}
              </div>
            )
          ) : batches.length === 0 ? (
            <EmptyState text={needle ? t('historyPage.noMatch') : t('history.scriptsEmpty')} />
          ) : (
            <div className="space-y-3">
              {batches.map((s) => (
                <ScriptBatch
                  key={s.script_id}
                  s={s}
                  open={Boolean(openScripts[s.script_id])}
                  onToggle={() =>
                    setOpenScripts((v) => ({ ...v, [s.script_id]: !v[s.script_id] }))
                  }
                />
              ))}
            </div>
          )}
        </PanelBody>
      </Panel>
    </div>
  )
}

/* --------------------------------------------------------------- pieces */

function PageTab({
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
        'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors',
        active ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
      <span className="text-[10px] text-muted-foreground">{count}</span>
    </button>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: typeof Video
  label: string
  value: string
  sub: string
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3.5 shadow-sm">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Icon className="h-3.5 w-3.5 text-primary" />
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold leading-none">{value}</div>
      <div className="mt-1 text-[10px] text-muted-foreground">{sub}</div>
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border py-12 text-center">
      <History className="h-6 w-6 text-muted-foreground/50" />
      <p className="text-xs text-muted-foreground">{text}</p>
    </div>
  )
}

function VideoCard({
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
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      {/* preview */}
      <div className="relative aspect-video w-full bg-black/90">
        {r.video_url ? (
          <video src={r.video_url} controls preload="metadata" playsInline className="h-full w-full object-contain" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1.5 text-muted-foreground">
            <Film className="h-7 w-7 opacity-40" />
            <span className="text-[11px]">{t('history.noVideo')}</span>
          </div>
        )}
      </div>

      <div className="space-y-2 p-3">
        <div className="flex items-center gap-2">
          <span className={cn('inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium', st.cls)}>
            <Icon className="h-3 w-3" />
            {t(`history.${r.status}`)}
          </span>
          <span className="truncate text-xs font-medium" title={r.name}>{r.name}</span>
          {r.kind === 'retry' ? (
            <Badge variant="outline" className="shrink-0 text-[10px]">{t('history.retryTag')}</Badge>
          ) : null}
          <span className="ml-auto shrink-0 text-[10px] text-muted-foreground" title={stamp(r.created_at)}>
            {timeAgo(r.created_at)}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
          {r.config.resolution ? <span>{r.config.resolution}</span> : null}
          {r.config.aspect ? <span>{r.config.aspect}</span> : null}
          {r.total_duration ? <span>{Math.round(r.total_duration)}s</span> : null}
          {r.cost > 0 ? <span>${r.cost.toFixed(2)}</span> : null}
          {r.scenes.length ? (
            <span className={bad > 0 ? 'text-amber-600' : undefined}>
              {ok}/{r.scenes.length} {t('history.ok')}
              {bad > 0 ? ` · ${bad} ${t('history.bad')}` : ''}
            </span>
          ) : null}
        </div>

        {r.warnings.length ? (
          <ul className="space-y-0.5 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-[10px] leading-relaxed text-amber-700 dark:text-amber-300">
            {r.warnings.slice(0, 3).map((w, i) => (
              <li key={i}>
                · {w.skipped ? t('historyPage.skipped') : t('historyPage.degraded')}
                {typeof w.scene_index === 'number' ? ` #${w.scene_index + 1}` : ''}
                {w.error ? ` — ${w.error}` : ''}
              </li>
            ))}
            {r.warnings.length > 3 ? <li>· +{r.warnings.length - 3}</li> : null}
          </ul>
        ) : null}

        <div className="flex items-center gap-2 pt-0.5">
          {r.video_url ? (
            <>
              <a href={r.video_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline">
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
          ) : null}
          {canRetry ? (
            <Button variant="secondary" size="sm" className="ml-auto h-7 px-2 text-[11px]" disabled={busy} onClick={() => onResume(r.render_id)}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
              {t('history.retry')}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function ScriptBatch({
  s,
  open,
  onToggle,
}: {
  s: ScriptHistoryRecord
  open: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState<string | null>(null)

  const copy = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(key)
      window.setTimeout(() => setCopied(null), 1200)
    } catch {
      /* clipboard unavailable (non-secure context) — ignore */
    }
  }

  return (
    <div className="rounded-xl border border-border bg-card shadow-sm">
      <button type="button" onClick={onToggle} className="flex w-full items-center gap-2 p-3 text-left">
        {open ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />}
        <FileText className="h-4 w-4 shrink-0 text-primary" />
        <span className="truncate text-sm font-medium" title={s.product}>{s.product || '—'}</span>
        <Badge variant="outline" className="shrink-0 text-[10px]">{s.language}</Badge>
        <span className="ml-auto flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground" title={stamp(s.created_at)}>
          <Clock className="h-3 w-3" />
          {timeAgo(s.created_at)}
        </span>
      </button>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 pb-2.5 text-[10px] text-muted-foreground">
        <span>{t('history.drafts', { n: s.drafts.length })}</span>
        {s.category ? <span>{s.category}</span> : null}
        {s.audience ? <span className="truncate">{s.audience}</span> : null}
        {s.tone ? <span>{s.tone}</span> : null}
        {s.traits ? (
          <span className="truncate" title={s.traits}>
            {t('brief.traits')}: {s.traits.split(/[\n,，;；/、]+/).filter(Boolean).slice(0, 3).join(' · ')}
          </span>
        ) : null}
        {s.selected_type ? (
          <Badge variant="secondary" className="bg-emerald-500/15 text-[10px] text-emerald-600">
            <Check className="h-3 w-3" />
            {t('historyPage.used')}: {s.selected_type}
          </Badge>
        ) : null}
      </div>

      {open ? (
        <div className="grid grid-cols-1 gap-3 border-t border-border p-3 lg:grid-cols-2">
          {s.drafts.map((d, i) => (
            <div key={`${d.type}-${i}`} className="rounded-lg border border-border bg-background/50 p-3">
              <div className="mb-1.5 flex items-center gap-2">
                <Badge variant="secondary" className="bg-primary/10 text-[10px] text-primary hover:bg-primary/15">
                  {d.title || d.type}
                </Badge>
                <button
                  type="button"
                  onClick={() => copy(`${s.script_id}-${i}`, d.narration)}
                  className="ml-auto inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
                  title={t('historyPage.copy')}
                >
                  {copied === `${s.script_id}-${i}` ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied === `${s.script_id}-${i}` ? t('historyPage.copied') : t('historyPage.copy')}
                </button>
              </div>
              <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-foreground/90">{d.narration}</p>
              {d.rationale ? <p className="mt-1.5 text-[10px] italic text-muted-foreground">{d.rationale}</p> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
