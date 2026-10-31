import { useTranslation } from 'react-i18next'
import { Camera, Clapperboard, FileText, SlidersHorizontal } from 'lucide-react'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import type { Plan, PlanScene, Settings } from '@/lib/api'
import { ANGLE_OPTIONS, SHOT_OPTIONS, cineLabel } from '@/lib/cine'
import { cn } from '@/lib/utils'

/** Distribute a target runtime across scenes proportional to their VO word
 *  counts, so the storyboard shows how many seconds each shot actually gets. */
function sceneSeconds(scenes: PlanScene[], targetSeconds: number | null): number[] {
  const words = scenes.map((s) => s.vo.trim().split(/\s+/).filter(Boolean).length)
  const totalWords = words.reduce((a, b) => a + b, 0)
  if (targetSeconds && targetSeconds > 0 && totalWords > 0) {
    return words.map((w) => Math.round((w / totalWords) * targetSeconds * 10) / 10)
  }
  return words.map((w) => Math.round((w / 2.6) * 10) / 10)
}

/**
 * Step 3/4 (render + refine): a READ-ONLY recap of everything about to be,
 * or already being, rendered. The edit gate lives one step back (the confirm
 * panel); once the user is on the render screen the script, storyboard and
 * config are shown for review only — no inputs, no surprises about what is
 * being paid for.
 */
export function RenderSummary({
  plan,
  settings,
  targetSeconds,
  aspect,
  style,
  resolution,
  videoProvider,
  withVoice,
  withCaptions,
}: {
  plan: Plan
  settings: Settings
  /** Ad runtime (seconds) chosen in the brief. */
  targetSeconds: number | null
  aspect: string
  style: string
  resolution: string
  videoProvider: string | null
  withVoice: boolean
  withCaptions: boolean
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const perScene = sceneSeconds(plan.scenes, targetSeconds)
  const totalWords = plan.scenes.reduce(
    (n, s) => n + s.vo.trim().split(/\s+/).filter(Boolean).length,
    0,
  )
  const estSeconds = targetSeconds && targetSeconds > 0 ? targetSeconds : Math.round(totalWords / 2.6)

  const styleLabel = t(`style.${style}`, { defaultValue: style })
  const aspectLabel = t(`aspect.${aspect}`, { defaultValue: aspect })
  const vendorLabel = videoProvider
    ? t(`vendor.${videoProvider}.label`, {
        defaultValue: settings.video.specs.find((s) => s.key === videoProvider)?.label ?? videoProvider,
      })
    : t('confirm.videoModelDefault')
  const dim = resolution

  return (
    <div className="space-y-5">
      {/* ---- config -------------------------------------------------- */}
      <Panel>
        <PanelHeader title={t('summary.config')} icon={SlidersHorizontal} subtitle={t('summary.configHint')} />
        <PanelBody className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <ConfigChip label={t('confirm.style')} value={styleLabel} />
            <ConfigChip label={t('script.aspect')} value={aspectLabel} />
            <ConfigChip label={t('confirm.resolution')} value={dim} />
            <ConfigChip
              label={t('confirm.videoModel')}
              value={vendorLabel}
            />
            <ConfigChip
              label={t('confirm.voice')}
              value={withVoice ? t('summary.on') : t('summary.off')}
              tone={withVoice ? 'good' : 'muted'}
            />
            <ConfigChip
              label={t('confirm.captions')}
              value={withCaptions ? t('summary.on') : t('summary.off')}
              tone={withCaptions ? 'good' : 'muted'}
            />
            {targetSeconds ? (
              <ConfigChip
                label={t('brief.duration')}
                value={`${targetSeconds}s`}
              />
            ) : null}
          </div>
          <p className="text-[11px] text-muted-foreground">
            {t('summary.length', { n: plan.scenes.length, s: estSeconds })}
          </p>
        </PanelBody>
      </Panel>

      {/* ---- ad script (read-only) ----------------------------------- */}
      <Panel>
        <PanelHeader title={t('summary.script')} icon={FileText} subtitle={t('summary.scriptHint')} />
        <PanelBody>
          <div className="space-y-1.5 rounded-lg border border-border bg-muted/30 p-3 text-sm leading-relaxed">
            {plan.scenes.map((s, i) => (
              <p key={s.id}>
                <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">
                  {String(i + 1).padStart(2, '0')}
                </span>
                {s.vo}
              </p>
            ))}
          </div>
        </PanelBody>
      </Panel>

      {/* ---- storyboard (read-only) ---------------------------------- */}
      <Panel>
        <PanelHeader
          title={t('summary.storyboard')}
          icon={Clapperboard}
          subtitle={t('summary.storyboardHint')}
          action={<Badge variant="outline">{t('confirm.count', { n: plan.scenes.length })}</Badge>}
        />
        <PanelBody>
          <div className="max-h-[460px] space-y-3 overflow-y-auto pr-1">
            {plan.scenes.map((s, i) => (
              <div key={s.id} className="rounded-lg border bg-background/40 p-3">
                <div className="mb-2 flex items-center gap-2">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand font-mono text-[11px] font-semibold text-white">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <Badge variant="secondary" className="bg-primary/10 text-primary hover:bg-primary/15">
                    {t(`beat.${s.beat}`, { defaultValue: s.beat })}
                  </Badge>
                  <Badge variant="outline" className="font-mono text-[10px]" title={t('confirm.secTitle')}>
                    ~{perScene[i]}s
                  </Badge>
                  <span className="ml-auto text-[11px] text-muted-foreground">{s.id}</span>
                </div>

                <p className="text-sm leading-relaxed text-foreground">{s.vo}</p>

                <div className="mt-2 rounded-md border border-dashed border-muted-foreground/30 bg-muted/20 p-2.5">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                    <Camera className="h-3.5 w-3.5 text-primary" />
                    {t('confirm.shot')} · {t('confirm.angle')} · {t('confirm.elements')}
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-[11px]">
                    <Meta label={t('confirm.shot')} value={cineLabel(SHOT_OPTIONS, s.shot, lang)} />
                    <Meta label={t('confirm.angle')} value={cineLabel(ANGLE_OPTIONS, s.angle, lang)} />
                    <div className="col-span-2">
                      <Meta
                        label={t('confirm.elements')}
                        value={(s.elements || []).join(', ') || '—'}
                      />
                    </div>
                  </div>
                </div>

                <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
                  <span className="text-muted-foreground/70">{t('confirm.prompt')}: </span>
                  {s.prompt}
                </p>
              </div>
            ))}
          </div>
        </PanelBody>
      </Panel>
    </div>
  )
}

function ConfigChip({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string
  tone?: 'default' | 'good' | 'muted'
}) {
  return (
    <div
      className={cn(
        'rounded-lg border px-2.5 py-1.5',
        tone === 'good' && 'border-emerald-500/30 bg-emerald-500/10',
        tone === 'muted' && 'border-border bg-muted/40',
        tone === 'default' && 'border-border bg-background',
      )}
    >
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-sm font-medium text-foreground">{value}</div>
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <div className="text-[10px] text-muted-foreground/70">{label}</div>
      <div className="text-foreground">{value}</div>
    </div>
  )
}
