import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Toaster, toast } from 'sonner'
import { AlertTriangle, Download, History as HistoryIcon, Settings as SettingsIcon, Sparkles, Terminal, Wand2 } from 'lucide-react'
import { Header } from '@/components/Header'
import { ProviderSettings } from '@/components/ProviderSettings'
import { type GeneratePayload } from '@/components/ScriptPanel'
import { BriefPanel } from '@/components/BriefPanel'
import { SceneConfirm } from '@/components/SceneConfirm'
import { RenderSummary } from '@/components/RenderSummary'
import { RenderHistory } from '@/components/RenderHistory'
import { HistoryPage } from '@/components/HistoryPage'
import { Stepper, type StepId } from '@/components/Stepper'
import { StatusCard } from '@/components/StatusCard'
import { PreviewCard } from '@/components/PreviewCard'
import { EditPanel } from '@/components/EditPanel'
import { DownloadPanel } from '@/components/DownloadPanel'
import { LogPanel } from '@/components/LogPanel'
import { TaskPanel, type ActiveTask, type TaskKind } from '@/components/TaskPanel'
import { Badge } from '@/components/ui/primitives'
import { Panel, PanelHeader, PanelBody } from '@/components/ui/panel'
import { useJob } from '@/hooks/useJob'
import {
  api, errorText, toastError, waitJob,
  type Job, type Plan, type PlanScene, type RenderRecord,
  type ScriptHistoryRecord, type ScriptsResponse, type Settings,
} from '@/lib/api'
import { cn } from '@/lib/utils'

type Page = 'settings' | 'history' | 'create'

export default function App() {
  const { t } = useTranslation()
  const [page, setPage] = useState<Page>('create')
  const [settings, setSettings] = useState<Settings | null>(null)
  const [lines, setLines] = useState<string[]>([])

  // --- creation flow ----------------------------------------------------
  const [step, setStep] = useState<StepId>('script')
  const [plan, setPlan] = useState<Plan | null>(null)
  const [hasScripts, setHasScripts] = useState(false)
  /** A conversational edit produced a new, refined video. */
  const [hasRefine, setHasRefine] = useState(false)
  /** The user actually exported the file (the export step really happened). */
  const [downloaded, setDownloaded] = useState(false)
  /** Confirmed render config + scenes, captured at confirm time so the
   *  read-only render summary can show exactly what is being paid for. */
  const [renderMeta, setRenderMeta] = useState<{
    scenes: PlanScene[]
    style: string
    resolution: string
    videoProvider: string | null
    withVoice: boolean
    withCaptions: boolean
    aspect: string
    targetSeconds: number | null
  } | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  /** The generation task currently running (scripts / plan / render). */
  const [task, setTask] = useState<ActiveTask | null>(null)
  /** Past renders kept by the server, newest first. */
  const [renders, setRenders] = useState<RenderRecord[]>([])
  /** Past script generations kept by the server, newest first. */
  const [scriptHistory, setScriptHistory] = useState<ScriptHistoryRecord[]>([])
  /** History id of the most recent script batch, used to record which of the
   *  four concepts the user actually moved forward with. */
  const lastScriptIdRef = useRef<string | null>(null)
  /** True while the history lists are being fetched. */
  const [historyLoading, setHistoryLoading] = useState(true)
  const [resuming, setResuming] = useState(false)
  /** Last payload used for step 1, so "re-parse" can redo it verbatim. */
  const [scriptPayload, setScriptPayload] = useState<GeneratePayload | null>(null)

  const onLog = useCallback((line: string) => {
    setLines((v) => [...v.slice(-400), line])
  }, [])

  const { job, running, watch } = useJob(onLog)

  useEffect(() => {
    api
      .getSettings()
      .then(setSettings)
      .catch((e) => toastError(e))
  }, [])

  const ready = Boolean(settings?.video.ready)

  /**
   * Run any generation job (scripts / plan / render) as a tracked task:
   * start it, watch it for live progress, and await its final result.
   * The TaskPanel reads `task` + `job` to render the progress UI.
   */
  const runJob = useCallback(
    async <T,>(kind: TaskKind, start: () => Promise<{ job_id: string }>): Promise<T> => {
      setTask({ kind, startedAt: Date.now() })
      try {
        const { job_id } = await start()
        watch(job_id)
        return await waitJob<T>(job_id)
      } finally {
        setTask(null)
      }
    },
    [watch],
  )

  // ---- step 0: brief -> 4 ad scripts ------------------------------------
  const runScripts = useCallback(
    (payload: Parameters<typeof api.scripts>[0]) =>
      runJob<ScriptsResponse>('scripts', () => api.scripts(payload)),
    [runJob],
  )

  // ---- step 1: script -> scene copy ------------------------------------
  const makePlan = async (p: GeneratePayload) => {
    if (!ready) {
      toast.error(t('errors.notConfigured'))
      setPage('settings')
      return
    }
    setScriptPayload(p)
    try {
      const r = await runJob<Plan>('plan', () =>
        api.plan({
          script: p.script,
          name: `web-${Date.now().toString(36)}`,
          style: p.style ?? 'dr_vsl',
          use_llm: p.use_llm,
          target_seconds: p.target_seconds ?? null,
          language: p.language,
          product: p.product ?? '',
          product_traits: p.product_traits ?? '',
        }),
      )
      setPlan(r)
      onLog(`> plan ${r.plan_id} · ${r.scenes.length} scenes`)
      // Record which of the four concepts actually went forward, so the
      // history page can show what was used (best-effort, never blocking).
      if (lastScriptIdRef.current && p.script_type) {
        api
          .selectScript(lastScriptIdRef.current, p.script_type, p.script)
          .catch(() => {})
      }
      setStep('confirm')
      toast.success(t('confirm.planned', { n: r.scenes.length }))
    } catch (e) {
      const msg = errorText(e)
      toast.error(msg)
      onLog(`ERROR: ${msg}`)
    }
  }

  const reparse = () => {
    if (scriptPayload) void makePlan(scriptPayload)
  }

  // ---- step 2 -> 3: confirm -> render (this is the step that spends) ----
  // Visual style + video model are picked in SceneConfirm at confirm time,
  // not in the brief — they only matter once pixels start costing money.
  const confirmScenes = async (
    scenes: PlanScene[],
    renderStyle: string,
    videoProvider: string | null,
    withVoice: boolean,
    withCaptions: boolean,
    resolution: string,
  ) => {
    if (!plan) return
    if (!ready) {
      toast.error(t('errors.notConfigured'))
      setPage('settings')
      return
    }
    const p = scriptPayload
    try {
      const r = await api.render({
        plan_id: plan.plan_id,
        scenes: scenes.map((s) => ({
          id: s.id,
          vo: s.vo,
          prompt: s.prompt,
          shot: s.shot,
          angle: s.angle,
          elements: s.elements,
        })),
        name: plan.name,
        style: renderStyle || plan.style,
        aspect: p?.aspect ?? '9:16',
        draft: p?.draft ?? false,
        video_provider: videoProvider ?? null,
        voice_provider: p?.voice_provider ?? null,
        target_seconds: p?.target_seconds ?? null,
        resolution: resolution || '1080p',
        reference_images: p?.reference_images ?? [],
        with_voice: withVoice,
        with_captions: withCaptions,
      })
      setRenderMeta({
        scenes,
        style: renderStyle || plan.style,
        resolution,
        videoProvider: videoProvider ?? null,
        withVoice,
        withCaptions,
        aspect: p?.aspect ?? '9:16',
        targetSeconds: p?.target_seconds ?? null,
      })
      setJobId(r.job_id)
      onLog(`> confirmed ${scenes.length} scenes · job ${r.job_id}`)
      setStep('render')
      watch(r.job_id)
    } catch (e) {
      const msg = errorText(e)
      toast.error(msg)
      onLog(`ERROR: ${msg}`)
    }
  }

  // ---- step 4: conversational edit -> partial re-render -----------------
  const refineJobRef = useRef<string | null>(null)
  const onEditRender = useCallback(
    (newJobId: string) => {
      setJobId(newJobId)
      refineJobRef.current = newJobId
      onLog(`> re-render job ${newJobId}`)
      setStep('refine')
      watch(newJobId)
    },
    [onLog, watch],
  )

  // A refine render counts as "refine done" only once it finishes without
  // errors — the flow bar must reflect artefacts, not button presses.
  useEffect(() => {
    if (!job?.done || job.error) return
    if (refineJobRef.current && job.job_id === refineJobRef.current) {
      setHasRefine(true)
    }
  }, [job?.done, job?.error, job?.job_id])

  // Advance to refine once the first render lands.
  useEffect(() => {
    if (job?.done && !job.error && step === 'render') setStep('refine')
  }, [job?.done, job?.error, step])

  /** Both halves of the history panel (videos + scripts) share one SQLite
   *  store server-side, so they are refreshed together. */
  const loadHistory = useCallback(
    (silent = false) => {
      if (!silent) setHistoryLoading(true)
      Promise.all([api.renders(), api.scriptHistory()])
        .then(([r, s]) => {
          setRenders(r.renders)
          setScriptHistory(s.scripts)
        })
        .catch(() => {})
        .finally(() => setHistoryLoading(false))
    },
    [],
  )

  // Keep the history fresh: on mount and whenever a tracked job finishes
  // (new scripts, a new video — or a resumed one — just landed).
  useEffect(() => {
    loadHistory()
  }, [loadHistory])
  useEffect(() => {
    if (job?.done) loadHistory(true)
  }, [job?.done, loadHistory])

  const onResumeRender = useCallback(
    async (renderId: string) => {
      setResuming(true)
      try {
        const r = await api.resumeRender(renderId)
        setJobId(r.job_id)
        onLog(`> resume job ${r.job_id}`)
        setStep((s) => (s === 'script' || s === 'confirm' ? 'render' : s))
        watch(r.job_id)
      } catch (e) {
        toastError(e)
      } finally {
        setResuming(false)
      }
    },
    [onLog, watch],
  )

  const finished: Job | null = job
  // "Render done" must be a *render* job's completion — the plan job also
  // finishes with done=true, and without the kind check the task panel
  // showed a green check on "Video render" right after planning.
  const hasVideo = Boolean(
    jobId && finished?.done && !finished?.error && (finished.kind === 'render' || finished.video_url),
  )

  /**
   * Flow-bar state, derived from artefacts only.
   *
   * A tick means "this thing exists". It used to mean "this step is before
   * the current one", which made clicking a node tick the ones above it.
   */
  const stepDone = useMemo<Record<StepId, boolean>>(
    () => ({
      script: hasScripts,
      confirm: Boolean(plan),
      render: hasVideo,
      refine: hasRefine,
      download: downloaded,
    }),
    [hasScripts, plan, hasVideo, hasRefine, downloaded],
  )

  /** Which steps the user may jump to yet (prerequisites met). */
  const stepUnlocked = useCallback(
    (s: StepId) => {
      switch (s) {
        case 'script':
          return true
        case 'confirm':
          return hasScripts || Boolean(plan)
        case 'render':
          return Boolean(renderMeta)
        case 'refine':
          return Boolean(jobId)
        case 'download':
          return hasVideo
      }
    },
    [hasScripts, plan, renderMeta, jobId, hasVideo],
  )

  return (
    <div className="relative min-h-screen bg-background">
      <Toaster position="top-center" richColors />

      {/* decorative brand backdrop */}
      <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="absolute -left-40 -top-40 h-[26rem] w-[26rem] rounded-full bg-primary/10 blur-3xl animate-pulse-soft" />
        <div className="absolute -right-32 top-1/4 h-[30rem] w-[30rem] rounded-full bg-violet-500/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 h-[24rem] w-[24rem] rounded-full bg-pink-500/10 blur-3xl" />
      </div>

      <Header ready={ready} />

      {/* --------------------------------------------------- page switcher */}
      <nav className="border-b bg-card/60 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] gap-1 px-6 pt-3">
          {(
            [
              ['create', Sparkles, t('nav.create')],
              ['history', HistoryIcon, t('nav.history')],
              ['settings', SettingsIcon, t('nav.settings')],
            ] as const
          ).map(([id, Icon, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setPage(id)}
              className={cn(
                '-mb-px flex items-center gap-1.5 rounded-t-lg border-b-2 px-3 py-2 text-xs font-medium transition-colors',
                page === id
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
              {id === 'settings' && !ready ? (
                <span className="ml-1 rounded-full bg-destructive/15 px-1.5 text-[10px] text-destructive">
                  !
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-[1400px] px-6 py-6">
        {/* ==================================================== history */}
        {page === 'history' ? (
          <HistoryPage
            renders={renders}
            scripts={scriptHistory}
            loading={historyLoading}
            busy={resuming}
            onResume={onResumeRender}
            onRefresh={loadHistory}
          />
        ) : page === 'settings' ? (
          <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(420px,560px)_1fr]">
            <div className="space-y-5">
              {settings ? (
                <>
                  <ProviderSettings settings={settings} onSaved={setSettings} />
                  <StatusCard settings={settings} />
                </>
              ) : (
                <div className="rounded-xl border bg-card p-5 text-sm text-muted-foreground shadow-sm">
                  Loading settings…
                </div>
              )}
            </div>
            <div className="space-y-5">
              <Panel>
                <PanelHeader title={t('settings.whatTitle')} icon={SettingsIcon} />
                <PanelBody className="space-y-3">
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {t('settings.whatBody')}
                  </p>
                  <ul className="space-y-1.5 text-xs text-muted-foreground">
                    <li>· {t('settings.point1')}</li>
                    <li>· {t('settings.point2')}</li>
                    <li>· {t('settings.point3')}</li>
                  </ul>
                </PanelBody>
              </Panel>
              <LogPanel lines={lines} />
            </div>
          </div>
        ) : (
          /* ==================================================== create */
          <div className="space-y-5">
            {step === 'script' && !plan ? <CreateHero /> : null}

            <div className="rounded-xl border bg-card px-4 py-3 shadow-sm">
              <Stepper current={step} done={stepDone} unlocked={stepUnlocked} onSelect={setStep} />
            </div>

            <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
              {/* -------------------------------------------------- left */}
              <div className="space-y-5">
                {/* The brief panel stays MOUNTED across step changes and is
                    merely hidden. Unmounting it threw away the four ad
                    scripts, the user's edits and the selected concept when
                    they came back from the storyboard step. */}
                {settings ? (
                  <div className={cn(step === 'script' ? 'animate-fade-in' : 'hidden')}>
                    <BriefPanel
                      settings={settings}
                      planBusy={task?.kind === 'plan'}
                      scriptsBusy={task?.kind === 'scripts'}
                      runScripts={async (payload) => {
                        const r = await runScripts(payload)
                        if (r.script_id) lastScriptIdRef.current = r.script_id
                        return r
                      }}
                      onScriptsReady={() => setHasScripts(true)}
                      onPlan={makePlan}
                    />
                  </div>
                ) : (
                  <div className="rounded-xl border bg-card p-5 text-sm text-muted-foreground shadow-sm">
                    Loading settings…
                  </div>
                )}

                {step === 'confirm' && plan && settings ? (
                  <div className="animate-fade-in">
                    <SceneConfirm
                      scenes={plan.scenes}
                      settings={settings}
                      busy={running || task !== null}
                      onBack={() => setStep('script')}
                      onReparse={reparse}
                      onConfirm={confirmScenes}
                      targetSeconds={scriptPayload?.target_seconds ?? null}
                    />
                  </div>
                ) : null}

                {(step === 'render' || step === 'refine') && settings ? (
                  <div className="animate-fade-in">
                    {renderMeta ? (
                      <RenderSummary
                        plan={{ ...plan!, scenes: renderMeta.scenes }}
                        settings={settings}
                        targetSeconds={renderMeta.targetSeconds}
                        aspect={renderMeta.aspect}
                        style={renderMeta.style}
                        resolution={renderMeta.resolution}
                        videoProvider={renderMeta.videoProvider}
                        withVoice={renderMeta.withVoice}
                        withCaptions={renderMeta.withCaptions}
                      />
                    ) : (
                      <div className="rounded-xl border bg-card p-5 text-sm text-muted-foreground shadow-sm">
                        {t('summary.loading')}
                      </div>
                    )}
                  </div>
                ) : null}

                {step === 'refine' ? (
                  <div className="animate-fade-in">
                    <Panel>
                      <PanelHeader title={t('edit.title')} icon={Wand2} />
                      <PanelBody>
                        <EditPanel jobId={jobId} onRender={onEditRender} />
                      </PanelBody>
                    </Panel>
                  </div>
                ) : null}

                {step === 'download' ? (
                  <div className="animate-fade-in">
                    <Panel>
                      <PanelHeader title={t('download.title')} icon={Download} />
                      <PanelBody>
                        <DownloadPanel
                          videoUrl={finished?.video_url ?? null}
                          onDownload={() => setDownloaded(true)}
                        />
                      </PanelBody>
                    </Panel>
                  </div>
                ) : null}
              </div>

              {/* ------------------------------------------------- right */}
              {/* Fixed layout: results + task progress always live here,
                  regardless of which step the left column is showing. On
                  large screens it stays pinned so a long scene list on the
                  left never leaves the progress panel stranded below. */}
              <div className="space-y-5 lg:sticky lg:top-[72px] lg:self-start">
                <TaskPanel
                  active={task}
                  job={job}
                  hasScripts={hasScripts}
                  hasPlan={Boolean(plan)}
                  hasVideo={hasVideo}
                />

                {jobId ? <PreviewCard job={finished} /> : null}

                {finished?.error ? (
                  <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{finished.error}</span>
                  </div>
                ) : null}

                <Panel>
                  <PanelHeader title={t('log.tab')} icon={Terminal} />
                  <PanelBody>
                    <LogPanel lines={lines} />
                  </PanelBody>
                </Panel>

                <RenderHistory
                  renders={renders}
                  scripts={scriptHistory}
                  busy={resuming}
                  onResume={onResumeRender}
                />

                {jobId ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">job {jobId}</Badge>
                    {finished?.done ? t('stage.done') : t('stage.queued')}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

/** Landing hero shown on the create page before a brief is drafted. */
function CreateHero() {
  const { t } = useTranslation()
  return (
    <div className="relative overflow-hidden rounded-xl border bg-brand-soft p-6 shadow-sm ring-1 ring-brand/20">
      <div className="flex items-start gap-4">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand text-white shadow-md">
          <Sparkles className="h-6 w-6" />
        </span>
        <div className="min-w-0">
          <h1 className="text-lg font-semibold leading-tight">{t('hero.title')}</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {t('hero.sub')}
          </p>
        </div>
      </div>
    </div>
  )
}
