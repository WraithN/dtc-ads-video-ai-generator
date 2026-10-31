import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { ExternalLink, KeyRound, Loader2, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge, Separator } from '@/components/ui/primitives'
import { api, errorText, toastError, type ProviderSpec, type Settings } from '@/lib/api'

const LLM_PRESETS: Record<string, { base_url: string; model: string }> = {
  deepseek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-flash' },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  moonshot: { base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  zhipu: { base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4.6' },
  qwen: { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
}

/** Sentinel for "type your own model id" -- Radix rejects empty option values. */
const CUSTOM = '__custom__'

/**
 * Model revision picker: a curated list plus "type your own".
 *
 * Vendors rev model identifiers constantly (Seedance, Kling and MiniMax all
 * have done it this year), so a stale dropdown is the single most common
 * failure here. The custom field is what keeps a new revision usable before
 * anyone ships a release.
 */
function ModelField({
  spec,
  value,
  onValue,
  t,
  labelKey = 'providers.model',
}: {
  spec: ProviderSpec | null
  value: string
  onValue: (v: string) => void
  t: TFunction
  labelKey?: string
}) {
  const known = spec?.models ?? []
  // "Custom" is sticky while the user has it open, but it must yield to any
  // value the parent hands down -- otherwise switching vendors leaves the
  // picker on "Custom" showing the previous vendor's id.
  const [pickedCustom, setPickedCustom] = useState(false)
  const custom = pickedCustom || (!!value && known.length > 0 && !known.includes(value))

  if (!spec || (!known.length && !spec.model_env)) return null

  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{t(labelKey)}</Label>
      <Select
        value={custom ? CUSTOM : value || known[0] || ''}
        onValueChange={(v) => {
          if (v === CUSTOM) {
            setPickedCustom(true)
            onValue('')
          } else {
            setPickedCustom(false)
            onValue(v)
          }
        }}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {known.map((m) => (
            <SelectItem key={m} value={m}>
              {m}
            </SelectItem>
          ))}
          <SelectItem value={CUSTOM}>{t('providers.modelCustom')}</SelectItem>
        </SelectContent>
      </Select>
      {custom || !known.length ? (
        <Input
          value={value}
          onChange={(e) => onValue(e.target.value)}
          placeholder={t('providers.modelCustomHint')}
          spellCheck={false}
        />
      ) : null}
    </div>
  )
}

/** Labels and hints live in i18n; the server copy is only a fallback. */
function vendorLabel(t: TFunction, kind: string, spec: ProviderSpec) {
  const prefix = kind === 'video' ? 'vendor' : kind === 'voice' ? 'voiceVendor' : 'llmVendor'
  return t(`${prefix}.${spec.key}.label`, { defaultValue: spec.label }) as string
}

function vendorNote(t: TFunction, kind: string, spec: ProviderSpec) {
  const prefix = kind === 'video' ? 'vendor' : kind === 'voice' ? 'voiceVendor' : 'llmVendor'
  return t(`${prefix}.${spec.key}.note`, { defaultValue: spec.notes }) as string
}

export function ProviderSettings({
  settings,
  onSaved,
}: {
  settings: Settings
  onSaved: (s: Settings) => void
}) {
  const { t } = useTranslation()
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const videoSpecs = settings.video.specs
  const voiceSpecs = settings.voice.specs
  const llmSpecs = settings.llm.specs

  // Land on the first vendor when nothing is configured, so the key field is
  // one click away instead of hiding behind "auto".
  const initialVideo =
    settings.video.selected === 'auto' && !settings.video.ready
      ? videoSpecs[0]?.key ?? 'auto'
      : settings.video.selected

  const [videoKey, setVideoKey] = useState(initialVideo)
  const [voiceKey, setVoiceKey] = useState(settings.voice.selected)
  const [llmKey, setLlmKey] = useState(settings.llm.selected)
  const [llmModel, setLlmModel] = useState(settings.llm.model)
  const [llmBase, setLlmBase] = useState(settings.llm.base_url)
  const [videoModel, setVideoModel] = useState('')
  const [voiceModel, setVoiceModel] = useState('')
  const [keys, setKeys] = useState<Record<string, string>>({})

  // Re-sync whenever the server sends a fresh snapshot (initial load, save).
  useEffect(() => {
    setVideoKey(initialVideo)
    setVoiceKey(settings.voice.selected)
    setLlmKey(settings.llm.selected)
    setLlmModel(settings.llm.model)
    setLlmBase(settings.llm.base_url)
    setKeys({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings])

  const videoSpec = useMemo(
    () => videoSpecs.find((s) => s.key === videoKey) ?? null,
    [videoSpecs, videoKey],
  )
  const voiceSpec = useMemo(
    () => voiceSpecs.find((s) => s.key === voiceKey) ?? null,
    [voiceSpecs, voiceKey],
  )
  const llmSpec = useMemo(() => llmSpecs.find((s) => s.key === llmKey) ?? null, [llmSpecs, llmKey])

  useEffect(() => {
    setVideoModel(videoSpec?.model_value || videoSpec?.models[0] || '')
  }, [videoSpec])

  useEffect(() => {
    // One voice id is shared across vendors in avb.toml, so a value that
    // does not belong to the vendor now on screen would silently mean
    // "unknown voice id" at synth time. Fall back to its first known voice.
    const current = voiceSpec?.model_value || ''
    const known = voiceSpec?.models ?? []
    setVoiceModel(known.includes(current) ? current : voiceSpec?.models[0] || '')
  }, [voiceSpec])

  const onPickLlm = (key: string) => {
    setLlmKey(key)
    const other = llmSpecs.filter((s) => s.key !== key)
    const preset = LLM_PRESETS[key]
    const next = llmSpecs.find((s) => s.key === key)
    // Don't leave the old vendor's model id / endpoint behind.
    if (preset && !(next?.models ?? []).includes(llmModel)) {
      setLlmModel(preset.model)
    }
    if (preset && (!llmBase || other.some((s) => LLM_PRESETS[s.key]?.base_url === llmBase))) {
      setLlmBase(preset.base_url)
    }
  }

  const save = async () => {
    setSaving(true)
    setMsg('')
    try {
      const fresh = await api.saveSettings({
        video_provider: videoKey,
        voice_provider: voiceKey,
        llm_provider: llmKey,
        llm_model: llmModel.trim() || null,
        llm_base_url: llmBase.trim() || null,
        video_model: videoSpec?.model_env ? videoModel.trim() || null : null,
        voice_model: voiceSpec?.model_env ? voiceModel.trim() || null : null,
        keys,
      })
      onSaved(fresh)
      const written = fresh.written?.env ?? []
      setMsg(
        written.length
          ? t('providers.saved', { keys: written.join(', ') })
          : t('providers.savedNone'),
      )
      if (!fresh.video.ready) setMsg((m) => `${m} · ${t('providers.notReadyAfterSave')}`)
    } catch (e) {
      const m = errorText(e)
      setMsg(t('providers.fail', { msg: m }))
      toastError(e)
    } finally {
      setSaving(false)
    }
  }

  const envFields = (spec: ProviderSpec | null) =>
    !spec
      ? null
      : Object.entries(spec.envs).map(([name, e]) => (
          <div key={name} className="space-y-1.5">
            <Label className="flex items-center gap-1.5 text-xs">
              <KeyRound className="h-3 w-3" />
              {t(`env.${name}.label`, { defaultValue: e.label })}
              {!e.required ? (
                <span className="font-normal text-muted-foreground">{t('providers.optional')}</span>
              ) : null}
            </Label>
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={keys[name] ?? ''}
              onChange={(ev) => setKeys((k) => ({ ...k, [name]: ev.target.value }))}
              placeholder={e.set ? t('providers.savedMask', { mask: e.masked }) : t(`env.${name}.hint`, { defaultValue: e.hint })}
            />
            {e.set ? (
              <p className="text-xs text-muted-foreground">{t('providers.keepEmpty')}</p>
            ) : e.hint ? (
              <p className="text-xs text-muted-foreground">{t(`env.${name}.hint`, { defaultValue: e.hint })}</p>
            ) : null}
          </div>
        ))

  return (
    <section className="rounded-lg border bg-card shadow-sm">
      <div className="flex items-start justify-between gap-3 p-5 pb-3">
        <div>
          <h2 className="text-sm font-semibold">{t('providers.title')}</h2>
          <p className="mt-1 text-xs text-muted-foreground">{t('providers.videoHint')}</p>
        </div>
        <Badge variant={settings.video.ready ? 'success' : 'destructive'}>
          {settings.video.ready ? t('status.ready') : t('providers.required')}
        </Badge>
      </div>

      <div className="space-y-4 px-5 pb-5">
        {/* ---------------------------------------------------------- video */}
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('providers.video')}</Label>
            <Select value={videoKey} onValueChange={setVideoKey}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {videoSpecs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {vendorLabel(t, 'video', s)} {s.ready ? '✓' : ''}
                  </SelectItem>
                ))}
                <SelectItem value="auto">{t('providers.auto')}</SelectItem>
              </SelectContent>
            </Select>
            {videoSpec ? (
              <p className="text-xs text-muted-foreground">
                {vendorNote(t, 'video', videoSpec)}
                {videoSpec.console ? (
                  <>
                    {' '}
                    <a
                      className="inline-flex items-center gap-0.5 text-primary hover:underline"
                      href={videoSpec.console}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t('providers.getKey')}
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </>
                ) : null}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">{t('providers.notesFallback')}</p>
            )}
          </div>

          <ModelField key={videoSpec?.key} spec={videoSpec} value={videoModel} onValue={setVideoModel} t={t} />

          {envFields(videoSpec)}
        </div>

        <Separator />

        {/* ---------------------------------------------------------- voice */}
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('providers.voice')}</Label>
            <Select value={voiceKey} onValueChange={setVoiceKey}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {voiceSpecs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {vendorLabel(t, 'voice', s)} {s.ready ? '✓' : ''}
                  </SelectItem>
                ))}
                <SelectItem value="auto">{t('voiceVendor.auto')}</SelectItem>
              </SelectContent>
            </Select>
            {voiceSpec ? (
              <p className="text-xs text-muted-foreground">{vendorNote(t, 'voice', voiceSpec)}</p>
            ) : null}
          </div>
          <ModelField
            key={voiceSpec?.key}
            spec={voiceSpec}
            value={voiceModel}
            onValue={setVoiceModel}
            t={t}
          />
          {envFields(voiceSpec)}
        </div>

        <Separator />

        {/* ------------------------------------------------------------ llm */}
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('providers.llm')}</Label>
            <Select value={llmKey} onValueChange={onPickLlm}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {llmSpecs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {vendorLabel(t, 'llm', s)} {s.ready ? '✓' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">{t('providers.baseUrl')}</Label>
              <Input value={llmBase} onChange={(e) => setLlmBase(e.target.value)} spellCheck={false} />
            </div>
            <ModelField
              key={llmSpec?.key}
              spec={llmSpec}
              value={llmModel}
              onValue={setLlmModel}
              t={t}
              labelKey="providers.modelId"
            />
          </div>
          {envFields(llmSpec)}
        </div>

        <Separator />

        <Button className="w-full" onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {saving ? t('providers.saving') : t('providers.save')}
        </Button>

        {msg ? <p className="text-xs text-muted-foreground">{msg}</p> : null}
        <p className="text-xs text-muted-foreground">{t('providers.hint')}</p>
      </div>
    </section>
  )
}
