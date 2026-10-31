import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Camera, Check, ClipboardCheck, Clapperboard, HelpCircle, Loader2, Pencil, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AutoTextarea } from '@/components/ui/auto-textarea'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import type { PlanScene, Settings } from '@/lib/api'
import { ANGLE_OPTIONS, SHOT_OPTIONS, type CineOption } from '@/lib/cine'
import { STYLES } from './ScriptPanel'
import { cn } from '@/lib/utils'

const RESOLUTIONS = ['540p', '720p', '1080p', '1440p', '4k']

function sceneSeconds(scenes: PlanScene[], targetSeconds: number | null): number[] {
  const words = scenes.map((s) => s.vo.trim().split(/\s+/).filter(Boolean).length)
  const totalWords = words.reduce((a, b) => a + b, 0)
  if (targetSeconds && targetSeconds > 0 && totalWords > 0) {
    return words.map((w) => Math.round((w / totalWords) * targetSeconds * 10) / 10)
  }
  return words.map((w) => Math.round((w / 2.6) * 10) / 10)
}

/**
 * Step 2: the confirmation gate.
 *
 * Everything here is editable and nothing is rendered until the user hits
 * confirm. That is the point -- the model's first pass at scene copy is a
 * draft, and paying for eight clips before reading the hook is how you burn
 * a budget. Visual style, resolution and the video model also live here: they
 * only matter at render time, so the brief step stays about the story.
 */
export function SceneConfirm({
  scenes,
  settings,
  busy,
  onBack,
  onReparse,
  onConfirm,
  targetSeconds,
}: {
  scenes: PlanScene[]
  settings: Settings
  busy: boolean
  onBack: () => void
  onReparse: () => void
  onConfirm: (
    scenes: PlanScene[],
    style: string,
    videoProvider: string | null,
    withVoice: boolean,
    withCaptions: boolean,
    resolution: string,
  ) => void
  /** Ad runtime (seconds) chosen in the brief; drives per-scene seconds. */
  targetSeconds: number | null
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const [draft, setDraft] = useState<PlanScene[]>(scenes)
  const [style, setStyle] = useState('dr_vsl')
  const [resolution, setResolution] = useState('1080p')
  const [videoModel, setVideoModel] = useState('__default__')
  const [withVoice, setWithVoice] = useState(true)
  const [withCaptions, setWithCaptions] = useState(true)

  // Re-seed when a fresh plan arrives (re-parse, or a new script).
  useEffect(() => setDraft(scenes), [scenes])

  const patch = (id: string, key: 'vo' | 'prompt' | 'shot' | 'angle', value: string) =>
    setDraft((v) => v.map((s) => (s.id === id ? { ...s, [key]: value } : s)))

  const patchElements = (id: string, raw: string) =>
    setDraft((v) =>
      v.map((s) =>
        s.id === id
          ? { ...s, elements: raw.split(',').map((x) => x.trim()).filter(Boolean) }
          : s,
      ),
    )

  const remove = (id: string) => setDraft((v) => v.filter((s) => s.id !== id))
  const move = (id: string, dir: -1 | 1) =>
    setDraft((v) => {
      const i = v.findIndex((s) => s.id === id)
      const j = i + dir
      if (i < 0 || j < 0 || j >= v.length) return v
      const next = [...v]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next.map((s, k) => ({ ...s, index: k }))
    })

  const totalWords = draft.reduce((n, s) => n + s.vo.trim().split(/\s+/).filter(Boolean).length, 0)
  // ~2.6 words/sec is the usual short-form VO rate; good enough to warn on.
  const estSeconds = Math.round(totalWords / 2.6)
  const perScene = sceneSeconds(draft, targetSeconds)

  return (
    <Panel>
      <PanelHeader
        title={t('confirm.title')}
        icon={ClipboardCheck}
        subtitle={t('confirm.subtitle')}
        action={
          <div className="flex items-center gap-2">
            <Badge variant="outline">{t('confirm.count', { n: draft.length })}</Badge>
            <Badge variant="outline">~{estSeconds}s</Badge>
          </div>
        }
      />
      <PanelBody className="space-y-3">
        <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
          {draft.map((s, i) => (
            <div key={s.id} className="rounded-lg border bg-background/40 p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand font-mono text-[11px] font-semibold text-white">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <Badge
                  variant="secondary"
                  className="bg-primary/10 text-primary hover:bg-primary/15"
                >
                  {t(`beat.${s.beat}`, { defaultValue: s.beat })}
                </Badge>
                <Badge variant="outline" className="font-mono text-[10px]" title={t('confirm.secTitle')}>
                  ~{perScene[i]}s
                </Badge>
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Pencil className="h-3 w-3" />
                  {s.id}
                </span>
                <div className="ml-auto flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-1.5 text-xs"
                    disabled={i === 0}
                    onClick={() => move(s.id, -1)}
                    title={t('confirm.up')}
                  >
                    ↑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-1.5 text-xs"
                    disabled={i === draft.length - 1}
                    onClick={() => move(s.id, 1)}
                    title={t('confirm.down')}
                  >
                    ↓
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-1.5 text-xs text-muted-foreground hover:text-destructive"
                    onClick={() => remove(s.id)}
                    title={t('confirm.remove')}
                  >
                    ✕
                  </Button>
                </div>
              </div>

              <div className="space-y-2">
                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground">
                    {t('confirm.vo')}
                  </Label>
                  <AutoTextarea
                    className="w-full text-sm leading-relaxed"
                    value={s.vo}
                    onChange={(e) => patch(s.id, 'vo', e.target.value)}
                  />
                  <p className="text-[11px] text-muted-foreground">{t('confirm.captionNote')}</p>
                </div>

                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground">
                    {t('confirm.prompt')}
                  </Label>
                  <AutoTextarea
                    className="w-full text-sm text-muted-foreground"
                    value={s.prompt}
                    onChange={(e) => patch(s.id, 'prompt', e.target.value)}
                  />
                </div>

                <div className="rounded-md border border-dashed border-muted-foreground/30 bg-muted/20 p-2.5">
                  <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                    <Camera className="h-3.5 w-3.5 text-primary" />
                    {t('confirm.shot')} · {t('confirm.angle')} · {t('confirm.elements')}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <CineSelect
                      label={t('confirm.shot')}
                      options={SHOT_OPTIONS}
                      value={s.shot}
                      lang={lang}
                      placeholder={t('confirm.shotPlaceholder')}
                      onChange={(v) => patch(s.id, 'shot', v)}
                    />
                    <CineSelect
                      label={t('confirm.angle')}
                      options={ANGLE_OPTIONS}
                      value={s.angle}
                      lang={lang}
                      placeholder={t('confirm.anglePlaceholder')}
                      onChange={(v) => patch(s.id, 'angle', v)}
                    />
                    <div className="col-span-2">
                      <CineField
                        label={t('confirm.elements')}
                        value={(s.elements || []).join(', ')}
                        onChange={(v) => patchElements(s.id, v)}
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Render-time picks: whole-film visual style + which video model
            spends the money + render resolution. All only take effect on
            confirm. Tooltips explain what each knob costs/does. */}
        <div className="space-y-2.5 rounded-lg border border-border bg-muted/40 p-3.5">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <Clapperboard className="h-4 w-4 text-primary" />
            {t('confirm.renderSetup')}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1 text-xs">
                {t('confirm.style')}
                <Tip title={t('confirm.styleTip')} />
              </Label>
              <Select value={style} onValueChange={setStyle}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STYLES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {t(`style.${s}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1 text-xs">
                {t('confirm.videoModel')}
                <Tip title={t('confirm.videoModelTip')} />
              </Label>
              <Select value={videoModel} onValueChange={setVideoModel}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__default__">{t('confirm.videoModelDefault')}</SelectItem>
                  {settings.video.specs.map((s) => (
                    <SelectItem key={s.key} value={s.key}>
                      {t(`vendor.${s.key}.label`, { defaultValue: s.label })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="col-span-2 space-y-1.5 sm:col-span-1">
              <Label className="flex items-center gap-1 text-xs">
                {t('confirm.resolution')}
                <Tip title={t('confirm.resolutionTip')} />
              </Label>
              <Select value={resolution} onValueChange={setResolution}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RESOLUTIONS.map((r) => (
                    <SelectItem key={r} value={r}>
                      {r}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">{t('confirm.renderSetupHint')}</p>

          {/* Audio / text layers — both optional. Captions are driven by the
              voiceover's word timings, so they are locked off when voice is. */}
          <div className="grid grid-cols-2 gap-3 pt-1">
            <ToggleRow
              label={t('confirm.voice')}
              hint={t('confirm.voiceHint')}
              checked={withVoice}
              onChange={(v) => {
                setWithVoice(v)
                if (!v) setWithCaptions(false)
              }}
            />
            <ToggleRow
              label={t('confirm.captions')}
              hint={withVoice ? t('confirm.captionsHint') : t('confirm.captionsLocked')}
              checked={withCaptions}
              disabled={!withVoice}
              onChange={setWithCaptions}
            />
          </div>
        </div>

        <div className="flex flex-col gap-2 pt-1 sm:flex-row">
          <Button variant="ghost" onClick={onBack} disabled={busy} className="sm:w-auto">
            {t('confirm.back')}
          </Button>
          <Button variant="secondary" onClick={onReparse} disabled={busy} className="sm:w-auto">
            <RefreshCw className={cn('h-4 w-4', busy && 'animate-spin')} />
            {t('confirm.reparse')}
          </Button>
          <Button
            className="bg-brand-btn hover:opacity-90 sm:ml-auto sm:w-auto"
            onClick={() =>
              onConfirm(
                draft,
                style,
                videoModel === '__default__' ? null : videoModel,
                withVoice,
                withCaptions,
                resolution,
              )
            }
            disabled={busy || draft.length === 0}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {t('confirm.confirm')}
          </Button>
        </div>

        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{t('confirm.spendNote')}</span>
        </div>
      </PanelBody>
    </Panel>
  )
}

function CineField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[10px] text-muted-foreground">{label}</Label>
      <input
        className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

/** Sentinel for "nothing selected" — Radix rejects an empty SelectItem value. */
const NO_VALUE = '__none__'

/**
 * Localised dropdown for a cinematography property (shot / angle).
 *
 * The stored value is always the canonical English term the parser and the
 * video prompt already understand, so switching the page language never
 * changes what gets rendered — only what the user reads. Values coming back
 * from the LLM that are outside the preset list are kept as a fallback option
 * instead of being silently dropped.
 */
function CineSelect({
  label,
  options,
  value,
  lang,
  placeholder,
  onChange,
}: {
  label: string
  options: CineOption[]
  value: string
  lang: string
  placeholder: string
  onChange: (v: string) => void
}) {
  const { t } = useTranslation()
  const current = (value || '').trim()
  const zh = lang.startsWith('zh')
  const match = options.find((o) => o.value.toLowerCase() === current.toLowerCase())
  const selected = match ? match.value : current ? current : NO_VALUE
  const emit = (v: string) => onChange(v === NO_VALUE ? '' : v)

  return (
    <div className="space-y-1">
      <Label className="text-[10px] text-muted-foreground">{label}</Label>
      <Select value={selected} onValueChange={emit}>
        <SelectTrigger className="h-7 text-xs">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {!match && current ? (
            <SelectItem value={current} className="text-xs italic">
              {current} · {t('confirm.customValue')}
            </SelectItem>
          ) : null}
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value} className="text-xs">
              {zh ? `${o.zh} · ${o.en}` : o.en}
            </SelectItem>
          ))}
          {selected !== NO_VALUE ? (
            <SelectItem value={NO_VALUE} className="text-xs text-muted-foreground">
              {placeholder}
            </SelectItem>
          ) : null}
        </SelectContent>
      </Select>
    </div>
  )
}

function ToggleRow({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string
  hint: string
  checked: boolean
  disabled?: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 rounded-md border border-border bg-background px-3 py-2',
        disabled && 'opacity-50',
      )}
    >
      <div className="min-w-0">
        <div className="text-xs font-medium text-foreground">{label}</div>
        <div className="truncate text-[10px] text-muted-foreground">{hint}</div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative h-5 w-9 shrink-0 rounded-full transition-colors',
          checked ? 'bg-primary' : 'bg-muted-foreground/30',
          !disabled && 'cursor-pointer',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform',
            checked && 'translate-x-4',
          )}
        />
      </button>
    </div>
  )
}

/** Tiny inline tooltip: a question-mark icon with a native title (works
 *  without a popover library; keeps config labels compact). */
function Tip({ title }: { title: string }) {
  if (!title) return null
  return (
    <span
      className="inline-flex cursor-help text-muted-foreground/60"
      title={title}
      aria-label={title}
    >
      <HelpCircle className="h-3 w-3" />
    </span>
  )
}
