import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Palette, Upload } from 'lucide-react'
import { api, errorText } from '@/lib/api'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'
import type { BrandDraft } from '@/lib/api'

const FONTS: { key: string; label: string }[] = [
  { key: '', label: 'Style default' },
  { key: 'ariblk', label: 'Arial Black' },
  { key: 'arialbd', label: 'Arial Bold' },
  { key: 'impact', label: 'Impact' },
  { key: 'segoeuib', label: 'Segoe UI Bold' },
  { key: 'calibrib', label: 'Calibri Bold' },
  { key: 'georgiab', label: 'Georgia Bold' },
  { key: 'consolab', label: 'Consolas Bold' },
]

/**
 * Brand constraint panel. Lets the user define a brand kit (colours, logo,
 * CTA card, font, caption style) and persists it per product. The chosen kit
 * is reported back to the parent, which sends it with the plan request so the
 * compositor keeps every render of the product on-style.
 */
export function BrandPanel({
  onChange,
}: {
  onChange: (b: BrandDraft | null, productKey: string) => void
}) {
  const { t } = useTranslation()
  const [productKey, setProductKey] = useState('')
  const [name, setName] = useState('')
  const [primary, setPrimary] = useState('#FF3B30')
  const [accent, setAccent] = useState('#FFE500')
  const [ink, setInk] = useState('#0B0B0F')
  const [paper, setPaper] = useState('#FFFFFF')
  const [logoPath, setLogoPath] = useState('')
  const [logoUrl, setLogoUrl] = useState<string | null>(null)
  const [fontKey, setFontKey] = useState('')
  const [ctaText, setCtaText] = useState('')
  const [ctaSub, setCtaSub] = useState('')
  const [watermark, setWatermark] = useState(true)
  const [captionPosition, setCaptionPosition] = useState('bottom')
  const [captionHighlight, setCaptionHighlight] = useState('')
  const [saving, setSaving] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const brand: BrandDraft = {
    name: name || undefined,
    primary,
    accent,
    ink,
    paper,
    logo_path: logoPath || undefined,
    font_key: fontKey || undefined,
    cta_text: ctaText || undefined,
    cta_sub: ctaSub || undefined,
    watermark,
    caption_position: captionPosition,
    caption_highlight: captionHighlight || undefined,
  }

  // Load the stored kit whenever the product key changes.
  useEffect(() => {
    let alive = true
    api
      .brandGet(productKey)
      .then((r) => {
        if (!alive) return
        const b = r.brand || {}
        setName(b.name ?? '')
        setPrimary(b.primary ?? '#FF3B30')
        setAccent(b.accent ?? '#FFE500')
        setInk(b.ink ?? '#0B0B0F')
        setPaper(b.paper ?? '#FFFFFF')
        setLogoPath(b.logo_path ?? '')
        setLogoUrl(null)
        setFontKey(b.font_key ?? '')
        setCtaText(b.cta_text ?? '')
        setCtaSub(b.cta_sub ?? '')
        setWatermark(b.watermark ?? true)
        setCaptionPosition(b.caption_position ?? 'bottom')
        setCaptionHighlight(b.caption_highlight ?? '')
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [productKey])

  // Report the current kit to the parent on every change.
  useEffect(() => {
    onChange(brand, productKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    name, primary, accent, ink, paper, logoPath, fontKey, ctaText, ctaSub,
    watermark, captionPosition, captionHighlight, productKey,
  ])

  const onUpload = async (file: File) => {
    try {
      const r = await api.uploadImages([file])
      const f = r.files?.[0]
      if (f) {
        setLogoPath(f.path)
        setLogoUrl(f.url)
        toast.success(t('brand.logoSet'))
      }
    } catch (e) {
      toast.error(errorText(e))
    }
  }

  const onSave = async () => {
    setSaving(true)
    try {
      await api.brandPut(productKey, brand)
      toast.success(t('brand.saved'))
    } catch (e) {
      toast.error(errorText(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel className="mt-5">
      <PanelHeader title={t('brand.title')} icon={Palette} />
      <PanelBody className="space-y-4">
        <p className="text-xs leading-relaxed text-muted-foreground">{t('brand.subtitle')}</p>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">{t('brand.productKey')}</label>
          <Input
            value={productKey}
            placeholder={t('brand.productKeyPlaceholder')}
            onChange={(e) => setProductKey(e.target.value)}
          />
          <p className="text-[11px] text-muted-foreground">{t('brand.productKeyHint')}</p>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">{t('brand.name')}</label>
          <Input value={name} placeholder={t('brand.namePlaceholder')} onChange={(e) => setName(e.target.value)} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <ColorField label={t('brand.primary')} value={primary} onChange={setPrimary} />
          <ColorField label={t('brand.accent')} value={accent} onChange={setAccent} />
          <ColorField label={t('brand.ink')} value={ink} onChange={setInk} />
          <ColorField label={t('brand.paper')} value={paper} onChange={setPaper} />
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">{t('brand.logo')}</label>
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void onUpload(f)
                e.target.value = ''
              }}
            />
            <Button variant="outline" size="sm" type="button" onClick={() => fileRef.current?.click()}>
              <Upload className="h-4 w-4" />
              {t('brand.logoUpload')}
            </Button>
            {logoUrl ? (
              <img src={logoUrl} alt="logo" className="h-8 rounded border bg-white object-contain px-1" />
            ) : logoPath ? (
              <span className="truncate text-[11px] text-muted-foreground">{logoPath}</span>
            ) : null}
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <Switch checked={watermark} onCheckedChange={setWatermark} />
            {t('brand.watermark')}
          </label>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">{t('brand.font')}</label>
          <select
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            value={fontKey}
            onChange={(e) => setFontKey(e.target.value)}
          >
            {FONTS.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">{t('brand.cta')}</label>
          <Input
            value={ctaText}
            placeholder={t('brand.ctaTextPlaceholder')}
            onChange={(e) => setCtaText(e.target.value)}
          />
          <Input
            value={ctaSub}
            placeholder={t('brand.ctaSubPlaceholder')}
            onChange={(e) => setCtaSub(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">{t('brand.captionPosition')}</label>
            <select
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={captionPosition}
              onChange={(e) => setCaptionPosition(e.target.value)}
            >
              <option value="bottom">Bottom</option>
              <option value="center">Center</option>
              <option value="top">Top</option>
            </select>
          </div>
          <ColorField label={t('brand.captionHighlight')} value={captionHighlight} onChange={setCaptionHighlight} allowEmpty />
        </div>

        <Button type="button" onClick={onSave} disabled={saving} className={cn('w-full')}>
          {saving ? t('brand.saving') : t('brand.save')}
        </Button>
      </PanelBody>
    </Panel>
  )
}

function ColorField({
  label,
  value,
  onChange,
  allowEmpty = false,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  allowEmpty?: boolean
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={value || '#000000'}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-9 shrink-0 cursor-pointer rounded-md border border-input bg-background"
        />
        {allowEmpty ? (
          <Input value={value} placeholder="inherit" onChange={(e) => onChange(e.target.value)} />
        ) : (
          <Input value={value} onChange={(e) => onChange(e.target.value)} />
        )}
      </div>
    </div>
  )
}
