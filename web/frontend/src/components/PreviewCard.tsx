import { useTranslation } from 'react-i18next'
import { AlertTriangle, Download, Film, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import type { Job } from '@/lib/api'
import { formatDuration, formatUsd } from '@/lib/utils'

export function PreviewCard({ job }: { job: Job | null }) {
  const { t } = useTranslation()
  const done = job?.done && job.video_url
  const rendering = job && !job.done && !job.error

  return (
    <Panel>
      <PanelHeader
        title={t('preview.title')}
        icon={Film}
        action={
          done ? (
            <Badge variant="success">{t('stage.done')}</Badge>
          ) : rendering ? (
            <Badge variant="outline" className="gap-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t('stage.rendering')}
            </Badge>
          ) : (
            <Badge variant="secondary">{t('preview.empty')}</Badge>
          )
        }
      />
      <PanelBody>
        <div className="overflow-hidden rounded-xl bg-black p-[2px] bg-brand shadow-sm">
          <div className="overflow-hidden rounded-[10px] bg-black">
            {done ? (
              <video
                key={job!.video_url!}
                className="mx-auto block max-h-[520px] w-full"
                controls
                src={job!.video_url!}
              />
            ) : (
              <div className="flex min-h-[380px] items-center justify-center bg-black/60 p-4">
                <div className="relative flex aspect-[9/16] h-[360px] max-w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-xl bg-muted/40 text-muted-foreground">
                  {rendering ? (
                    <>
                      <div className="absolute inset-0 animate-shimmer" />
                      <Loader2 className="relative h-8 w-8 animate-spin text-primary" />
                      <span className="relative text-sm">{t('stage.rendering')}…</span>
                    </>
                  ) : (
                    <>
                      <Film className="h-8 w-8" />
                      <span className="text-sm">{t('preview.empty')}</span>
                      <span className="text-xs">{t('preview.emptySub')}</span>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {done ? (
          <>
            <div className="mt-4 grid grid-cols-3 gap-3">
              <Stat value={formatDuration(job!.total_duration)} label={t('preview.duration')} />
              <Stat value={formatUsd(job!.cost)} label={t('preview.cost')} />
              <Stat value={String(job!.scenes ?? '—')} label={t('preview.scenes')} />
            </div>
            {job!.video_url ? (
              <a
                href={job!.video_url}
                download={(job!.video_url.split('/').pop() || 'ad.mp4').replace(/[?#].*$/, '')}
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
              >
                <Download className="h-4 w-4" />
                {t('download.button')}
              </a>
            ) : null}
            <p className="mt-3 text-xs text-muted-foreground">
              {t('preview.rendered', { n: job!.rendered ?? 0, m: job!.reused ?? 0 })} ·{' '}
              {t('preview.reusedHint')}
            </p>
            {job!.warnings && job!.warnings.length > 0 ? (
              <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  {job!.warnings.some((w) => w.scope === 'account')
                    ? t('preview.degradedAccount', { n: job!.warnings.length })
                    : t('preview.degraded', { n: job!.warnings.length })}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {job!.warnings.some((w) => w.scope === 'account')
                    ? t('preview.degradedAccountHint')
                    : t('preview.degradedHint')}
                </p>
                <ul className="mt-1.5 space-y-0.5">
                  {job!.warnings.map((w) => (
                    <li key={w.scene_id} className="text-xs text-muted-foreground">
                      <span className="font-medium">#{w.index}</span>{' '}
                      {w.skipped ? (
                        <span className="italic">{t('preview.sceneSkipped')}</span>
                      ) : (
                        <span className="line-clamp-2 break-all">{w.error}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
      </PanelBody>
    </Panel>
  )
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-lg bg-brand-soft px-3 py-2.5 text-center ring-1 ring-brand/10">
      <div className="text-base font-semibold">{value}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  )
}
