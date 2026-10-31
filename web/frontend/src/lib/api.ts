import { toast } from 'sonner'
import i18n from '@/i18n'

export interface EnvSpec {
  label: string
  secret: boolean
  required: boolean
  hint: string
  default: string
  set: boolean
  masked: string
}

export interface ProviderSpec {
  key: string
  label: string
  kind: string
  ready: boolean
  missing: string[]
  console: string
  notes: string
  models: string[]
  model_env: string
  model_value: string
  envs: Record<string, EnvSpec>
  selected: boolean
}

export interface Settings {
  video: {
    selected: string
    resolved: string
    active: string | null
    ready: boolean
    error: string
    specs: ProviderSpec[]
  }
  voice: { selected: string; resolved: string; active: string; ready: boolean; specs: ProviderSpec[] }
  llm: {
    selected: string
    model: string
    base_url: string
    api_key_env: string
    api_key_set: boolean
    api_key_masked: string
    specs: ProviderSpec[]
  }
  env_file: string
  toml_file: string
  written?: { toml: string[]; env: string[] }
}

export interface JobStats {
  phase?: string
  elapsed?: number
  prompt_chars?: number
  task_id?: string
  status?: string
  received_mb?: number
  provider?: string
  model?: string
  scene_index?: number
  scene_total?: number
  scene_id?: string
  cached?: boolean
  [k: string]: unknown
}

/** A scene that failed video generation and fell back to a solid backdrop. */
export interface SceneWarning {
  scene_id: string
  index: number
  error: string
  /** 'account' = balance/auth failure (subsequent scenes skipped); 'scene' = single-shot failure. */
  scope?: 'account' | 'scene'
  /** True when this scene was skipped entirely due to an earlier account-level failure. */
  skipped?: boolean
}

export interface RenderSceneStatus {
  id: string
  index: number
  status: 'ok' | 'degraded' | 'skipped'
}

export interface RenderRecord {
  render_id: string
  job_id: string
  plan_id: string
  name: string
  created_at: number
  status: 'done' | 'partial' | 'failed'
  video_url: string | null
  video_path: string | null
  config: {
    style: string
    resolution: string
    aspect: string
    video_provider: string | null
    target_seconds: number | null
    with_voice: boolean
    with_captions: boolean
  }
  scenes: RenderSceneStatus[]
  cost: number
  total_duration: number
  warnings: { scene_index?: number; error?: string; scope?: string; skipped?: boolean }[]
  parent_id: string | null
  kind: 'render' | 'retry'
}

export interface Job {
  job_id: string
  kind?: 'scripts' | 'plan' | 'render' | string
  stage: string
  progress: number
  message: string
  done: boolean
  error: string | null
  video_url: string | null
  /** Absolute local path of the rendered video, if known. */
  video_path?: string | null
  cost: number | null
  total_duration?: number
  scenes?: number
  rendered?: number
  reused?: number
  /** Scenes degraded to a solid backdrop after video generation failed. */
  warnings?: SceneWarning[]
  /** Live detail for the task panel (submit bytes, vendor task id, ...). */
  stats?: JobStats | null
  /** Final payload for generation jobs (scripts / plan). */
  result?: {
    scripts?: ScriptDraft[]
    brief?: { product: string; style: string }
    plan_id?: string
    name?: string
    style?: string
    scenes?: PlanScene[]
  } | null
}

export interface PlanScene {
  id: string
  index: number
  beat: string
  /** Spoken line. Also drives the kinetic caption (one source of truth). */
  vo: string
  prompt: string
  motion: string
  style: string
  shot: string
  angle: string
  elements: string[]
}

export interface ScriptDraft {
  type: string
  title: string
  narration: string
  rationale: string
  outline?: { beat: string; line: string }[]
}

export interface ScriptHistoryRecord {
  script_id: string
  job_id: string
  product: string
  category: string | null
  audience: string | null
  tone: string | null
  language: string
  /** What the product physically is — kept so a past brief can be reused. */
  traits?: string
  created_at: number
  drafts: ScriptDraft[]
  selected_type: string | null
  selected_narration: string | null
}

export interface ScriptsResponse {
  brief: { product: string; style: string; traits?: string }
  scripts: ScriptDraft[]
  /** History id of this batch (== generating job id), used for write-back. */
  script_id?: string
  /** False when the offline templates were used instead of the model. */
  used_llm?: boolean
  /** Why the model was not used (shown to the user, never swallowed). */
  fallback_reason?: string
}

export interface UploadedImage {
  /** Absolute server path — send this back on /api/render. */
  path: string
  name: string
  /** Static URL for the thumbnail preview. */
  url: string
  size: number
}

export interface Plan {
  plan_id: string
  name: string
  style: string
  scenes: PlanScene[]
}

export interface EditPlanResult {
  intent: string
  scene_ids: string[]
  ops: { path: string; value: unknown }[]
  explanation: string
  job_id?: string
  rebuilt?: string[]
}

async function json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await fetch(input, init)
  const text = await r.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!r.ok) {
    const detail = (data as { detail?: string } | null)?.detail ?? r.statusText
    throw new Error(detail)
  }
  return data as T
}

export const api = {
  getSettings: () => json<Settings>('/api/settings'),

  saveSettings: (payload: {
    video_provider?: string | null
    voice_provider?: string | null
    llm_provider?: string | null
    llm_model?: string | null
    llm_base_url?: string | null
    video_model?: string | null
    voice_model?: string | null
    keys?: Record<string, string>
  }) =>
    json<Settings>('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /**
   * Top of the funnel: brief -> four competing ad-script concepts.
   * Runs as a background job; poll with waitJob() for the ScriptsResponse.
   */
  scripts: (payload: {
    product: string
    selling_points: string[] | string
    /** Optional: scripts no longer bake in a visual style; it is picked at render time. */
    style?: string
    audience?: string | null
    tone?: string | null
    category?: string | null
    use_llm: boolean
    /** Bump on "regenerate" so the LLM produces a fresh, different batch. */
    variation?: number
    /** Language the ad scripts should be written in (e.g. "zh", "en"). */
    language: string
    /** What the product physically IS — category, material, craft, flavour,
     *  spec. Without it the model writes copy that fits any product at all. */
    product_traits?: string
  }) =>
    json<{ job_id: string }>('/api/scripts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /**
   * Step 1: script -> scenes. Free; nothing is generated yet.
   * Background job; waitJob() resolves to the Plan.
   */
  plan: (payload: {
    script: string
    name: string
    style: string
    use_llm: boolean
    target_seconds?: number | null
    language?: string
    /** Threads a "subject on screen" anchor into every visual prompt. */
    product?: string
    product_traits?: string
  }) =>
    json<{ job_id: string }>('/api/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  getPlan: (id: string) => json<Plan>(`/api/plan/${id}`),

  /**
   * Product reference images (three-view set) for image-guided video
   * generation. Multipart upload; returns paths to attach to the render.
   */
  uploadImages: (files: File[]) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    return json<{ files: UploadedImage[] }>('/api/upload', {
      method: 'POST',
      body: fd,
    })
  },

  /** Step 3: render the confirmed scenes. This is the step that spends money. */
  render: (payload: {
    plan_id: string
    scenes: { id: string; vo: string; prompt: string; shot?: string; angle?: string; elements?: string[] }[]
    name: string
    style: string
    aspect: string
    draft: boolean
    video_provider: string | null
    voice_provider: string | null
    target_seconds?: number | null
    resolution?: string
    reference_images?: string[]
    with_voice?: boolean
    with_captions?: boolean
  }) =>
    json<{ job_id: string }>('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  generate: (payload: {
    script: string
    name: string
    style: string
    aspect: string
    draft: boolean
    use_llm: boolean
    video_provider: string | null
    voice_provider: string | null
  }) =>
    json<{ job_id: string }>('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  getJob: (id: string) => json<Job>(`/api/jobs/${id}`),

  planEdit: (payload: {
    job_id: string
    instruction: string
    use_llm: boolean
    apply?: boolean
    draft?: boolean
  }) =>
    json<EditPlanResult>('/api/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /** Render history: every past render (and resume) the server has kept. */
  renders: () => json<{ renders: RenderRecord[] }>('/api/renders'),

  renderDetail: (id: string) => json<RenderRecord>(`/api/renders/${id}`),

  /** Script generation history: every past brief -> 4 scripts run. */
  scriptHistory: () => json<{ scripts: ScriptHistoryRecord[] }>('/api/scripts-history'),

  scriptHistoryDetail: (id: string) =>
    json<ScriptHistoryRecord>(`/api/scripts-history/${id}`),

  /** Mark which of a batch's four concepts was taken forward. */
  selectScript: (id: string, type: string, narration: string) =>
    json<{ ok: boolean }>(`/api/scripts-history/${id}/select`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, narration }),
    }),

  /** Resume a partial/failed render from the failure point. Returns a new job. */
  resumeRender: (render_id: string) =>
    json<{ job_id: string }>('/api/render/resume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ render_id }),
    }),
}

/** Toast on failure, return the message so callers can also show it inline. */
export function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e)
}

export function toastError(e: unknown) {
  toast.error(errorText(e))
}

/** Localised label for a stage id returned by the backend. */
export function stageLabel(stage: string) {
  return i18n.t(`stage.${stage}`, { defaultValue: stage }) as string
}

/**
 * Poll a generation job until it finishes; resolve with its `result`.
 * The live view of the same job comes from useJob() (websocket); this is
 * the promise-side counterpart so callers can `await` the payload.
 */
export async function waitJob<T>(jobId: string, intervalMs = 1000): Promise<T> {
  for (;;) {
    let j: Job
    try {
      j = await api.getJob(jobId)
    } catch {
      await new Promise((r) => setTimeout(r, intervalMs))
      continue // transient network hiccup; keep waiting
    }
    if (j.done) {
      if (j.error) throw new Error(j.error)
      return (j.result ?? {}) as T
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}
