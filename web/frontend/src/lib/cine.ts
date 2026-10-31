/**
 * Cinematography presets shared by the editable storyboard and the read-only
 * render summary.
 *
 * The canonical `value` is always the English term the parser, the LLM and the
 * video prompt already speak — switching the page language must never change
 * what actually gets rendered. Only the rendered *label* is localised.
 */

export interface CineOption {
  value: string
  zh: string
  en: string
}

export const SHOT_OPTIONS: CineOption[] = [
  { value: 'extreme close-up', zh: '极特写', en: 'Extreme close-up' },
  { value: 'close-up', zh: '特写', en: 'Close-up' },
  { value: 'medium close-up', zh: '中近景', en: 'Medium close-up' },
  { value: 'medium shot', zh: '中景', en: 'Medium shot' },
  { value: 'wide shot', zh: '远景', en: 'Wide shot' },
  { value: 'establishing shot', zh: '定场镜头', en: 'Establishing shot' },
  { value: 'two-shot', zh: '双人镜头', en: 'Two-shot' },
  { value: 'over-the-shoulder', zh: '过肩镜头', en: 'Over-the-shoulder' },
  { value: 'point-of-view', zh: '主观视角（POV）', en: 'Point of view (POV)' },
  { value: 'product hero shot', zh: '产品主图', en: 'Product hero shot' },
  { value: 'tracking shot', zh: '跟拍', en: 'Tracking shot' },
  { value: 'handheld shot', zh: '手持镜头', en: 'Handheld shot' },
  { value: 'insert shot', zh: '特写插入', en: 'Insert shot' },
]

export const ANGLE_OPTIONS: CineOption[] = [
  { value: 'eye-level', zh: '平视', en: 'Eye-level' },
  { value: 'low-angle', zh: '仰拍', en: 'Low-angle' },
  { value: 'high-angle', zh: '俯拍', en: 'High-angle' },
  { value: "bird's-eye", zh: '鸟瞰', en: "Bird's-eye" },
  { value: 'top-down', zh: '顶视', en: 'Top-down' },
  { value: 'dutch angle', zh: '荷兰角（倾斜）', en: 'Dutch angle' },
  { value: 'over-the-shoulder', zh: '过肩', en: 'Over-the-shoulder' },
  { value: 'front', zh: '正面', en: 'Front' },
  { value: 'side', zh: '侧面', en: 'Side' },
  { value: 'back', zh: '背面', en: 'Back' },
  { value: "worm's-eye", zh: '贴地仰拍', en: "Worm's-eye" },
]

/** Find the matching preset, case-insensitively. */
export function findCine(options: CineOption[], value: string): CineOption | undefined {
  const v = (value || '').trim().toLowerCase()
  if (!v) return undefined
  return options.find((o) => o.value.toLowerCase() === v)
}

/** Human-readable label for a stored value, localised when it is a preset. */
export function cineLabel(options: CineOption[], value: string, lang: string): string {
  const raw = (value || '').trim()
  if (!raw) return '—'
  const hit = findCine(options, raw)
  if (!hit) return raw
  return lang.startsWith('zh') ? hit.zh : hit.en
}
