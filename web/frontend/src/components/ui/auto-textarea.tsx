import { useLayoutEffect, useRef } from 'react'
import type { TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * Auto-growing textarea: the height tracks the content, so long copy is never
 * clipped behind a fixed box.
 *
 * This matters for the shot list. A fixed 56px box silently hid the tail of
 * every Chinese line (CJK is information-dense: 20 characters fill a line,
 * 40 do not fit), which made the storyboard look like it was missing half the
 * script. The box grows instead, and shrinks back when the text is edited
 * down.
 */
export function AutoTextarea({
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const value = rest.value
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])
  return (
    <textarea
      ref={ref}
      className={cn('resize-none overflow-hidden', className)}
      {...rest}
    />
  )
}
