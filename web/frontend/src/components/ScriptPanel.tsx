import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Progress, Switch } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import type { Settings } from '@/lib/api'
import { stageLabel } from '@/lib/api'

export const STYLES = ['dr_vsl', 'comic', 'vector', 'claymation', 'talking_head', 'cinematic']
export const ASPECTS = ['9:16', '1:1', '4:5', '16:9']

export interface GeneratePayload {
  script: string
  /** Which of the four frameworks the user picked, echoed back to history. */
  script_type?: string
  /** Optional now: style is picked at the confirm/render step. */
  style?: string
  aspect: string
  draft: boolean
  use_llm: boolean
  video_provider: string | null
  voice_provider: string | null
  target_seconds: number | null
  reference_images?: string[]
  /** Language the script was written in (keeps the storyboard in the same
   *  language as the copy). */
  language?: string
  /** Product identity — threaded into the storyboard's visual prompts so the
   *  video model keeps the same product across every shot. */
  product?: string
  product_traits?: string
}

export function ScriptPanel({
  settings,
  running,
  job,
  onPlan,
}: {
  settings: Settings
  running: boolean
  job: { stage: string; progress: number; message: string } | null
  /** Step 1 only produces scene copy; nothing is rendered yet. */
  onPlan: (p: GeneratePayload) => void
}) {
  const { t } = useTranslation()
  const [script, setScript] = useState('')
  const [style, setStyle] = useState('dr_vsl')
  const [aspect, setAspect] = useState('9:16')
  const [draft, setDraft] = useState(false)
  const [useLlm, setUseLlm] = useState(true)
  const [videoOverride, setVideoOverride] = useState('__default__')
  const [voice, setVoice] = useState(settings.voice.selected === 'auto' ? 'edge' : settings.voice.selected)

  const submit = () => {
    if (!script.trim()) return
    onPlan({
      script: script.trim(),
      style,
      aspect,
      draft,
      use_llm: useLlm,
      video_provider: videoOverride === '__default__' ? null : videoOverride,
      voice_provider: voice === 'auto' ? null : voice,
      target_seconds: null,
    })
  }

  return (
    <Panel>
      <PanelHeader title={t('script.title')} icon={Wand2} />
      <PanelBody className="space-y-4">
        <Textarea
          className="min-h-[168px] leading-relaxed"
          placeholder={t('script.placeholder')}
          value={script}
          onChange={(e) => setScript(e.target.value)}
        />

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('script.style')}</Label>
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
            <Label className="text-xs">{t('script.aspect')}</Label>
            <Select value={aspect} onValueChange={setAspect}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ASPECTS.map((a) => (
                  <SelectItem key={a} value={a}>
                    {t(`aspect.${a}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs">{t('script.overrideVideo')}</Label>
            <Select value={videoOverride} onValueChange={setVideoOverride}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__default__">{t('script.overrideVideoHint')}</SelectItem>
                {settings.video.specs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {t(`vendor.${s.key}.label`, { defaultValue: s.label })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">{t('script.voice')}</Label>
            <Select value={voice} onValueChange={setVoice}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {settings.voice.specs.map((s) => (
                  <SelectItem key={s.key} value={s.key}>
                    {t(`voiceVendor.${s.key}.label`, { defaultValue: s.label })}
                  </SelectItem>
                ))}
                <SelectItem value="auto">{t('voiceVendor.auto')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-2 rounded-lg border border-dashed bg-muted/30 p-3">
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>{t('script.useLlm')}</span>
            <Switch checked={useLlm} onCheckedChange={setUseLlm} />
          </label>
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>{t('script.draft')}</span>
            <Switch checked={draft} onCheckedChange={setDraft} />
          </label>
        </div>

        <Button className="w-full bg-brand hover:opacity-90" onClick={submit} disabled={running || !script.trim()}>
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
          {running ? t('script.planning') : t('script.plan')}
        </Button>

        {running && job ? (
          <div className="space-y-1.5">
            <Progress value={Math.round((job.progress ?? 0) * 100)} />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                {stageLabel(job.stage)} · {job.message}
              </span>
              <span>{Math.round((job.progress ?? 0) * 100)}%</span>
            </div>
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  )
}
