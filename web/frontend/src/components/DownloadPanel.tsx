import { useTranslation } from 'react-i18next'
import { Download, UploadCloud } from 'lucide-react'

export function DownloadPanel({
  videoUrl,
  onDownload,
}: {
  videoUrl: string | null
  /** Fired when the user actually exports the file — the flow bar uses it to
   *  mark the export step as done. */
  onDownload?: () => void
}) {
  const { t } = useTranslation()

  const fileName = videoUrl
    ? (videoUrl.split('/').pop() || 'ad.mp4').replace(/[?#].*$/, '')
    : 'ad.mp4'

  return (
    <div className="space-y-4">
      <p className="text-xs leading-relaxed text-muted-foreground">{t('download.hint')}</p>

      {videoUrl ? (
        <a
          href={videoUrl}
          download={fileName}
          onClick={() => onDownload?.()}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
        >
          <Download className="h-4 w-4" />
          {t('download.button')}
        </a>
      ) : (
        <p className="text-xs text-muted-foreground">{t('download.needVideo')}</p>
      )}

      <div className="rounded-lg border border-border bg-background/40 p-3">
        <div className="flex items-center gap-2 text-xs font-medium text-foreground">
          <UploadCloud className="h-4 w-4 text-primary" />
          {t('download.whereTitle')}
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
          {t('download.whereBody')}
        </p>
      </div>
    </div>
  )
}
