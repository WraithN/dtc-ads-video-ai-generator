import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Clapperboard, HelpCircle, ImagePlus, Leaf, Loader2, Minus, Plus, RefreshCw, Sparkles, ThumbsUp, Wand2, X, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/input'
import { AutoTextarea } from '@/components/ui/auto-textarea'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { LANGUAGES } from '@/i18n'
import { api, toastError, type ScriptDraft, type Settings, type UploadedImage } from '@/lib/api'
const TYPE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  pain_solution: Zap,
  grass_planting: Leaf,
  review_proof: ThumbsUp,
  story_drama: Clapperboard,
}

const DURATION_MIN = 5
const DURATION_MAX = 60
const DURATION_STEP = 5

const clampDuration = (v: number) =>
  Math.min(DURATION_MAX, Math.max(DURATION_MIN, Math.round(v)))

const SAMPLES: { product: string; category: string[]; points: string }[] = [
  {
    product: 'Lumo sleep lamp',
    category: ['家居日用', '小家电'],
    points: 'falls asleep 2x faster\nwarm sunrise wake-up light\nno blue light after dark',
  },
  {
    product: 'Pico portable blender',
    category: ['小家电'],
    points: 'blend anywhere, USB-C\n14 oz cup doubles as bottle\ncrushes ice in 20s',
  },
]

/** Preset chips for tone / audience / category: click to fill, still free-form. */
const TONE_PRESETS_KEY = 'brief.tonePresets'
const AUDIENCE_PRESETS_KEY = 'brief.audiencePresets'
const CATEGORY_PRESETS_KEY = 'brief.categoryPresets'

/**
 * Step 1 (new): product brief -> four competing ad-script concepts.
 *
 * Replaces the free-text script box. The user describes the product, we
 * return four frameworks (pain→solution, grass-planting, review, story),
 * they pick one and can edit the narration, then we push that narration
 * into the existing plan/confirm/render pipeline.
 */
export function BriefPanel({
  planBusy,
  scriptsBusy,
  runScripts,
  onScriptsReady,
  onPlan,
}: {
  settings: Settings
  planBusy: boolean
  scriptsBusy: boolean
  /** Brief -> 4 scripts, run as a tracked background job by the app shell. */
  runScripts: (payload: {
    product: string
    selling_points: string[] | string
    style?: string
    audience: string | null
    tone: string | null
    category?: string | null
    use_llm: boolean
    variation?: number
    language: string
    /** What the product physically is. */
    product_traits?: string
  }) => Promise<{ scripts: ScriptDraft[]; used_llm?: boolean; fallback_reason?: string }>
  onScriptsReady: () => void
  onPlan: (p: {
    script: string
    /** Which framework the user picked — written back to script history. */
    script_type?: string
    style?: string
    aspect: string
    draft: boolean
    use_llm: boolean
    video_provider: string | null
    voice_provider: string | null
    target_seconds: number | null
    reference_images?: string[]
    /** Language the copy was written in. */
    language: string
    /** Product identity, threaded into the storyboard's visual prompts. */
    product: string
    product_traits: string
  }) => void
}) {
  const { t, i18n } = useTranslation()
  const [product, setProduct] = useState('')
  const [category, setCategory] = useState<string[]>([])
  const [points, setPoints] = useState('')
  /**
   * What the product physically IS — category, material, craft, flavour,
   * spec, price band. Selling points say what it does for the buyer; this
   * says what it IS, and it is the difference between a script about baijiu
   * and a script that could be about anything at all.
   */
  const [traits, setTraits] = useState('')
  const [audience, setAudience] = useState<string[]>([])
  const [tone, setTone] = useState<string[]>([])
  const [useLlm, setUseLlm] = useState(true)
  const [targetSec, setTargetSec] = useState(30)
  const [genCount, setGenCount] = useState(0)
  /** Set when the offline templates were used instead of the model. */
  const [fallback, setFallback] = useState<string | null>(null)
  // Script language defaults to the current page language so copy lands in the
  // user's own market; they can still override per-brief.
  const [language, setLanguage] = useState(
    i18n.language?.startsWith('zh') ? 'zh' : 'en',
  )

  // Product three-view reference images. Purely additive: empty list = the
  // old text-to-video behaviour, so nothing breaks if the user skips them.
  const [refImages, setRefImages] = useState<UploadedImage[]>([])
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const MAX_REFS = 9

  const addRefImages = async (files: FileList | null) => {
    if (!files?.length) return
    const room = MAX_REFS - refImages.length
    const picked = Array.from(files).slice(0, Math.max(0, room))
    if (!picked.length) {
      toastError(new Error(t('brief.refMax', { n: MAX_REFS })))
      return
    }
    setUploading(true)
    try {
      const r = await api.uploadImages(picked)
      setRefImages((v) => [...v, ...r.files].slice(0, MAX_REFS))
    } catch (e) {
      toastError(e)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const [drafts, setDrafts] = useState<ScriptDraft[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})

  const generate = async (variation = 0) => {
    if (!product.trim() && !points.trim()) {
      toastError(new Error(t('brief.needInput')))
      return
    }
    try {
      const r = await runScripts({
        product: product.trim(),
        selling_points: points,
        audience: audience.length ? audience.join('、') : null,
        tone: tone.length ? tone.join('、') : null,
        category: category.length ? category.join('、') : null,
        use_llm: useLlm,
        variation,
        language,
        product_traits: traits.trim(),
      })
      setDrafts(r.scripts)
      setEdited(Object.fromEntries(r.scripts.map((s) => [s.type, s.narration])))
      setSelected(r.scripts[0]?.type ?? null)
      setGenCount(variation)
      // Say it out loud when the templates were used. The user must not
      // mistake offline filler for something the model thinks about their
      // product.
      setFallback(r.used_llm === false ? r.fallback_reason || 'unknown' : null)
      onScriptsReady()
    } catch (e) {
      toastError(e)
    }
  }

  const narrationOf = (s: ScriptDraft) => edited[s.type] ?? s.narration

  const next = () => {
    const pick = drafts.find((s) => s.type === selected)
    if (!pick) return
    onPlan({
      script: narrationOf(pick).trim(),
      script_type: pick.type,
      aspect: '9:16',
      draft: false,
      use_llm: useLlm,
      // Style and video model are chosen at the confirm step now.
      style: 'dr_vsl',
      video_provider: null,
      voice_provider: null,
      target_seconds: targetSec,
      reference_images: refImages.map((r) => r.path),
      language,
      product: product.trim(),
      product_traits: traits.trim(),
    })
  }

  const applySample = (s: { product: string; category: string[]; points: string }) => {
    setProduct(s.product)
    setCategory([...s.category])
    setPoints(s.points)
  }

  return (
    <Panel>
      <PanelHeader title={t('brief.title')} icon={Wand2} />
      <PanelBody className="space-y-4">
        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.language')}</Label>
          <Select value={language} onValueChange={setLanguage}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGES.map((l) => (
                <SelectItem key={l.code} value={l.code}>
                  {l.label}
                </SelectItem>
              ))}
              {/* Allow languages beyond the two UI locales (the LLM can write
                  any of them); only shown if the current value isn't listed. */}
              {!LANGUAGES.some((l) => l.code === language) ? (
                <SelectItem value={language}>{language}</SelectItem>
              ) : null}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.product')}</Label>
          <Textarea
            className="min-h-[52px]"
            placeholder={t('brief.productPlaceholder')}
            value={product}
            onChange={(e) => setProduct(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            <span className="text-[11px] text-muted-foreground">{t('brief.sampleHint')}</span>
            {SAMPLES.map((s, i) => (
              <button
                key={i}
                type="button"
                onClick={() => applySample(s)}
                className="rounded-full border border-dashed border-muted-foreground/30 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
              >
                {s.product}
              </button>
            ))}
          </div>
        </div>

        <TagSelect
          label={t('brief.category')}
          placeholder={t('brief.categoryPlaceholder')}
          hint={t('brief.tagHint')}
          presets={t(CATEGORY_PRESETS_KEY, { returnObjects: true, defaultValue: [] }) as string[]}
          value={category}
          onChange={setCategory}
        />

        {/* Product traits: what the product IS, not what it does for the
            buyer. This is the field that lets the model tell a bottle of
            baijiu from a sleep lamp — and it also becomes the "subject on
            screen" anchor in every storyboard visual prompt. */}
        <div className="space-y-1.5">
          <Label className="flex items-center gap-1 text-xs">
            {t('brief.traits')}
            <Tip title={t('brief.traitsTip')} />
          </Label>
          <Textarea
            className="min-h-[80px]"
            placeholder={t('brief.traitsPlaceholder')}
            value={traits}
            onChange={(e) => setTraits(e.target.value)}
          />
          <p className="text-[11px] text-muted-foreground">{t('brief.traitsHint')}</p>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.points')}</Label>
          <Textarea
            className="min-h-[80px]"
            placeholder={t('brief.pointsPlaceholder')}
            value={points}
            onChange={(e) => setPoints(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-2 items-start gap-3">
          <TagSelect
            label={t('brief.audience')}
            placeholder={t('brief.tagPlaceholder')}
            hint={t('brief.tagHint')}
            presets={t(AUDIENCE_PRESETS_KEY, { returnObjects: true, defaultValue: [] }) as string[]}
            value={audience}
            onChange={setAudience}
          />
          <TagSelect
            label={t('brief.tone')}
            placeholder={t('brief.tagPlaceholder')}
            hint={t('brief.tagHint')}
            presets={t(TONE_PRESETS_KEY, { returnObjects: true, defaultValue: [] }) as string[]}
            value={tone}
            onChange={setTone}
          />
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.duration')}</Label>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-9 w-9 shrink-0"
            disabled={targetSec <= DURATION_MIN}
            onClick={() => setTargetSec((v) => clampDuration(v - DURATION_STEP))}
            title={`-${DURATION_STEP}s`}
          >
            <Minus className="h-4 w-4" />
          </Button>
          <div className="relative flex-1">
            <input
              type="number"
              min={DURATION_MIN}
              max={DURATION_MAX}
              step={1}
              value={targetSec}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10)
                if (!Number.isNaN(v)) setTargetSec(v)
              }}
              onBlur={() => setTargetSec((v) => clampDuration(v))}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
            />
          </div>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-9 w-9 shrink-0"
            disabled={targetSec >= DURATION_MAX}
            onClick={() => setTargetSec((v) => clampDuration(v + DURATION_STEP))}
            title={`+${DURATION_STEP}s`}
          >
            <Plus className="h-4 w-4" />
          </Button>
          <span className="shrink-0 text-sm text-muted-foreground">秒</span>
        </div>
          <p className="text-[11px] text-muted-foreground">{t('brief.durationHint')}</p>
        </div>

        {/* Product reference images: three-view uploads that steer the
            video provider (MiniMax H3 v2 reference-to-video). */}
        <div className="space-y-1.5">
          <Label className="text-xs">{t('brief.refTitle')}</Label>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
            multiple
            hidden
            onChange={(e) => void addRefImages(e.target.files)}
          />
          <div className="flex flex-wrap gap-2">
            {refImages.map((img) => (
              <div
                key={img.path}
                className="group relative h-16 w-16 overflow-hidden rounded-lg border bg-muted"
              >
                <img src={img.url} alt={img.name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  aria-label={`remove ${img.name}`}
                  onClick={() => setRefImages((v) => v.filter((r) => r.path !== img.path))}
                  className="absolute right-0.5 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
            {refImages.length < MAX_REFS ? (
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                disabled={uploading}
                className="flex h-16 w-16 flex-col items-center justify-center gap-1 rounded-lg border border-dashed text-muted-foreground transition-colors hover:border-primary hover:text-foreground disabled:opacity-50"
                title={t('brief.refMax', { n: MAX_REFS })}
              >
                {uploading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ImagePlus className="h-4 w-4" />
                )}
                <span className="text-[10px] leading-none">{t('brief.refAdd')}</span>
              </button>
            ) : null}
          </div>
          <p className="text-[11px] text-muted-foreground">{t('brief.refHint')}</p>
        </div>

        <div className="space-y-2 rounded-lg border border-dashed bg-muted/30 p-3">
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>{t('brief.useLlm')}</span>
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
              className="h-4 w-4 accent-[hsl(var(--primary))]"
            />
          </label>
          <p className="text-[11px] text-muted-foreground">{t('brief.useLlmHint')}</p>
        </div>

        <Button className="w-full bg-brand-btn hover:opacity-90" onClick={() => generate(0)} disabled={scriptsBusy}>
          {scriptsBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {scriptsBusy ? t('brief.generating') : t('brief.generate')}
        </Button>

        {drafts.length ? (
          <div className="space-y-3 pt-1">
            {fallback ? (
              <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-700 dark:text-amber-300">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  {t('brief.fallbackTitle')}{' '}
                  <span className="font-mono text-[11px] opacity-80">{fallback}</span>
                  <br />
                  {t('brief.fallbackBody')}
                </span>
              </div>
            ) : null}
            <p className="text-xs font-medium text-muted-foreground">{t('brief.choose')}</p>
            {drafts.map((s) => {
              const active = selected === s.type
              const Icon = TYPE_ICONS[s.type]
              // A real <button> would nest the editable <textarea> inside
              // interactive-inside-interactive (invalid HTML), and clicks on
              // the textarea never reached the selection handler -- which is
              // why picking a card kept the first one selected. A div with
              // proper a11y wiring + textarea focus selects the card.
              return (
                <div
                  key={s.type}
                  role="button"
                  tabIndex={0}
                  aria-pressed={active}
                  onClick={() => setSelected(s.type)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setSelected(s.type)
                    }
                  }}
                  className={cn(
                    'block w-full cursor-pointer rounded-lg border p-3 text-left transition-all',
                    active
                      ? 'border-primary bg-brand-soft shadow-sm ring-1 ring-primary/30'
                      : 'border-border bg-background/40 hover:bg-background',
                  )}
                >
                  <div className="mb-2 flex items-center gap-2">
                    <span
                      className={cn(
                        'flex h-5 w-5 items-center justify-center rounded-full border text-[10px]',
                        active
                          ? 'border-transparent bg-primary text-primary-foreground'
                          : 'border-muted-foreground/40 text-muted-foreground',
                      )}
                    >
                      {active ? '✓' : ''}
                    </span>
                    {Icon ? (
                      <Icon className={cn('h-4 w-4', active ? 'text-primary' : 'text-muted-foreground')} />
                    ) : null}
                    <span className="text-sm font-semibold">
                      {t(`scriptType.${s.type}`, { defaultValue: s.title })}
                    </span>
                    <Badge variant="outline" className="ml-auto text-[10px]">
                      {s.rationale}
                    </Badge>
                  </div>
                  <AutoTextarea
                    className="w-full cursor-text border-transparent bg-transparent text-xs leading-relaxed focus-visible:border-input focus-visible:bg-background"
                    value={narrationOf(s)}
                    onClick={(e) => e.stopPropagation()}
                    onFocus={() => setSelected(s.type)}
                    onChange={(e) =>
                      setEdited((v) => ({ ...v, [s.type]: e.target.value }))
                    }
                  />
                </div>
              )
            })}

            <Button
              className="w-full bg-brand-btn hover:opacity-90"
              onClick={next}
              disabled={!selected || planBusy}
            >
              {planBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              {planBusy ? t('brief.planning') : t('brief.toScenes')}
            </Button>
            <Button
              className="w-full"
              variant="ghost"
              onClick={() => generate(genCount + 1)}
              disabled={scriptsBusy || planBusy}
            >
              {scriptsBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {scriptsBusy ? t('brief.generating') : t('brief.regenerate')}
            </Button>
          </div>
        ) : null}
      </PanelBody>
    </Panel>
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

/**
 * Multi-select tag field: pick from presets (toggle) or type a custom tag and
 * press Enter to create it. Selected tags render as removable chips. Used for
 * audience and tone, which are many-valued.
 */
function TagSelect({
  label,
  placeholder,
  hint,
  presets,
  value,
  onChange,
}: {
  label: string
  placeholder: string
  hint?: string
  presets: string[]
  value: string[]
  onChange: (v: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const toggle = (tag: string) => {
    if (value.includes(tag)) onChange(value.filter((t) => t !== tag))
    else onChange([...value, tag])
  }
  const addCustom = () => {
    const v = draft.trim()
    if (v && !value.includes(v)) onChange([...value, v])
    setDraft('')
  }
  const available = (presets || []).filter((p) => !value.includes(p))
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{label}</Label>
      {value.length ? (
        <div className="flex flex-wrap gap-1.5">
          {value.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-[11px] text-primary"
            >
              {t}
              <button
                type="button"
                aria-label={`remove ${t}`}
                onClick={() => onChange(value.filter((x) => x !== t))}
                className="text-primary/60 transition-colors hover:text-primary"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            addCustom()
          }
        }}
        placeholder={placeholder}
        className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
      />
      {available.length ? (
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {available.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => toggle(p)}
              className="rounded-full border border-dashed border-muted-foreground/30 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary hover:text-foreground"
            >
              {p}
            </button>
          ))}
        </div>
      ) : null}
      {hint ? <p className="text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  )
}
