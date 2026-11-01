import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Download, Eye, Layers, Plus, RefreshCw } from 'lucide-react'
import {
  api, errorText, type VariantSet, type VariantSetSummary,
  type CreateVariantSetPayload,
} from '@/lib/api'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { AutoTextarea } from '@/components/ui/auto-textarea'
import { cn } from '@/lib/utils'

const TEMPLATES = ['pain_hook', 'ugc_talk', 'product_hero', 'comparison', 'talking_expert']
const STYLES = ['dr_vsl', 'ugc', 'comic', 'vector', 'cinematic']
const VOICES = ['adam', 'aria', 'bella', 'antoni']
const FRAMEWORKS = ['pain_solution', 'review_proof', 'grass_planting', 'story_drama']

/**
 * A/B variant lab. Takes one product + one script and produces N distinct
 * SceneGraph IRs (template x style x voice x framework). Generation is
 * plan-level only — no TTS, no video — so it is free and synchronous. The
 * resulting set can be compared side by side and exported as a zip of IRs for
 * downstream A/B testing.
 */
export function VariantsPage() {
  const { t } = useTranslation()
  const [product, setProduct] = useState('')
  const [name, setName] = useState('')
  const [script, setScript] = useState('')
  const [count, setCount] = useState(5)
  const [language, setLanguage] = useState('en')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [templates, setTemplates] = useState<string[]>([])
  const [styles, setStyles] = useState<string[]>([])
  const [voices, setVoices] = useState<string[]>([])
  const [frameworks, setFrameworks] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [current, setCurrent] = useState<VariantSet | null>(null)
  const [past, setPast] = useState<VariantSetSummary[]>([])
  const [pastLoading, setPastLoading] = useState(true)

  const loadPast = useCallback(() => {
    setPastLoading(true)
    api
      .variantSets()
      .then((r) => setPast(r.sets))
      .catch(() => {})
      .finally(() => setPastLoading(false))
  }, [])

  useEffect(() => {
    loadPast()
  }, [loadPast])

  const toggle = (setter: Dispatch<SetStateAction<string[]>>, value: string) =>
    setter((prev) => (prev.includes(value) ? prev.filter((x) => x !== value) : [...prev, value]))

  const generate = async () => {
    if (!script.trim()) {
      toast.error(t('variants.needScript'))
      return
    }
    const payload: CreateVariantSetPayload = {
      product: product.trim(),
      name: name.trim() || undefined,
      script: script.trim(),
      count,
      language,
      templates: templates.length ? templates : undefined,
      styles: styles.length ? styles : undefined,
      voices: voices.length ? voices : undefined,
      frameworks: frameworks.length ? frameworks : undefined,
    }
    setBusy(true)
    try {
      const set = await api.createVariantSet(payload)
      setCurrent(set)
      toast.success(t('variants.generated', { n: set.members.length }))
      loadPast()
    } catch (e) {
      toast.error(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const viewSet = async (id: string) => {
    try {
      setCurrent(await api.variantSet(id))
    } catch (e) {
      toast.error(errorText(e))
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl border bg-card px-4 py-3 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Layers className="h-4 w-4 text-brand" />
          {t('variants.title')}
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t('variants.subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        {/* -------------------------------------------------- generator + comparison */}
        <div className="space-y-5">
          <Panel>
            <PanelHeader title={t('variants.title')} icon={Layers} />
            <PanelBody className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">{t('variants.product')}</label>
                  <Input
                    value={product}
                    placeholder={t('variants.productPlaceholder')}
                    onChange={(e) => setProduct(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">{t('variants.name')}</label>
                  <Input
                    value={name}
                    placeholder={t('variants.namePlaceholder')}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t('variants.script')}</label>
                <AutoTextarea
                  value={script}
                  placeholder={t('variants.scriptPlaceholder')}
                  onChange={(e) => setScript(e.target.value)}
                  className="min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">{t('variants.count')}</label>
                  <Input
                    type="number"
                    min={1}
                    max={20}
                    value={count}
                    onChange={(e) => setCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">{t('variants.language')}</label>
                  <select
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                  >
                    <option value="en">English</option>
                    <option value="zh">中文</option>
                  </select>
                </div>
              </div>

              <p className="text-[11px] leading-relaxed text-muted-foreground">{t('variants.brandNote')}</p>

              <div className="space-y-3 rounded-lg border border-border bg-background/40 p-3">
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
                >
                  <Plus className={cn('h-3.5 w-3.5 transition-transform', showAdvanced && 'rotate-45')} />
                  {t('variants.advanced')}
                </button>
                {showAdvanced ? (
                  <div className="space-y-3">
                    <p className="text-[11px] text-muted-foreground">{t('variants.advancedHint')}</p>
                    <ChipMultiSelect label={t('variants.templates')} options={TEMPLATES} selected={templates} onToggle={(v) => toggle(setTemplates, v)} />
                    <ChipMultiSelect label={t('variants.styles')} options={STYLES} selected={styles} onToggle={(v) => toggle(setStyles, v)} />
                    <ChipMultiSelect label={t('variants.voices')} options={VOICES} selected={voices} onToggle={(v) => toggle(setVoices, v)} />
                    <ChipMultiSelect label={t('variants.frameworks')} options={FRAMEWORKS} selected={frameworks} onToggle={(v) => toggle(setFrameworks, v)} />
                  </div>
                ) : null}
              </div>

              <Button type="button" onClick={generate} disabled={busy} className="w-full">
                {busy ? t('variants.generating') : t('variants.generate')}
              </Button>
            </PanelBody>
          </Panel>

          {current ? (
            <Panel className="animate-fade-in">
              <PanelHeader title={t('variants.comparison')} icon={Layers} />
              <PanelBody className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">
                    {t('variants.generated', { n: current.members.length })}
                    {current.product ? ` · ${current.product}` : ''}
                  </span>
                  <a
                    href={api.variantSetExportUrl(current.set_id)}
                    download
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
                  >
                    <Download className="h-4 w-4" />
                    {t('variants.exportZip')}
                  </a>
                </div>
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full min-w-[560px] text-left text-xs">
                    <thead className="bg-muted/50 text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 font-medium">{t('variants.colIndex')}</th>
                        <th className="px-3 py-2 font-medium">{t('variants.colTemplate')}</th>
                        <th className="px-3 py-2 font-medium">{t('variants.colStyle')}</th>
                        <th className="px-3 py-2 font-medium">{t('variants.colVoice')}</th>
                        <th className="px-3 py-2 font-medium">{t('variants.colFramework')}</th>
                        <th className="px-3 py-2 text-right font-medium">{t('variants.colScenes')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {current.members.map((m) => (
                        <tr key={m.member_id} className="border-t border-border">
                          <td className="px-3 py-2 font-medium">{m.idx + 1}</td>
                          <td className="px-3 py-2">{m.template}</td>
                          <td className="px-3 py-2">{m.style}</td>
                          <td className="px-3 py-2">{m.voice}</td>
                          <td className="px-3 py-2">{m.framework}</td>
                          <td className="px-3 py-2 text-right">{m.scenes}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PanelBody>
            </Panel>
          ) : null}
        </div>

        {/* -------------------------------------------------- past sets */}
        <div className="space-y-5 lg:sticky lg:top-[72px] lg:self-start">
          <Panel>
            <PanelHeader
              title={t('variants.pastTitle')}
              icon={Layers}
              action={
                <button
                  type="button"
                  onClick={loadPast}
                  className="text-muted-foreground hover:text-foreground"
                  title={t('historyPage.refresh')}
                >
                  <RefreshCw className="h-4 w-4" />
                </button>
              }
            />
            <PanelBody className="space-y-2">
              {pastLoading ? (
                <p className="text-xs text-muted-foreground">{t('historyPage.loading')}</p>
              ) : past.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t('variants.pastEmpty')}</p>
              ) : (
                past.map((s) => (
                  <div
                    key={s.set_id}
                    className="flex items-center justify-between gap-2 rounded-lg border border-border bg-background/40 p-2.5"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium">{s.name}</div>
                      <div className="truncate text-[11px] text-muted-foreground">
                        {s.product || '—'} · {t('variants.members', { n: s.member_count })}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <Button variant="outline" size="sm" type="button" onClick={() => viewSet(s.set_id)}>
                        <Eye className="h-3.5 w-3.5" />
                        {t('variants.view')}
                      </Button>
                      <a
                        href={api.variantSetExportUrl(s.set_id)}
                        download
                        className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-input bg-background px-3 text-xs font-medium shadow-sm hover:bg-accent hover:text-accent-foreground"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </a>
                    </div>
                  </div>
                ))
              )}
            </PanelBody>
          </Panel>
        </div>
      </div>
    </div>
  )
}

function ChipMultiSelect({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string
  options: string[]
  selected: string[]
  onToggle: (v: string) => void
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = selected.includes(o)
          return (
            <button
              key={o}
              type="button"
              onClick={() => onToggle(o)}
              className={cn(
                'rounded-full border px-2.5 py-1 text-xs transition-colors',
                on
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-input text-muted-foreground hover:bg-accent/60',
              )}
            >
              {o}
            </button>
          )
        })}
      </div>
    </div>
  )
}
