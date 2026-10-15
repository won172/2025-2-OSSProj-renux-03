import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react'

const SELECTOR = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Stable focus lifecycle: typing or an inline callback changing must not refocus a modal. */
export function useFocusTrap(open: boolean, container: RefObject<HTMLElement | null>, onClose: () => void, busy = false) {
  const latest = useRef({ onClose, busy })
  useLayoutEffect(() => { latest.current = { onClose, busy } }, [onClose, busy])
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusables = () => Array.from(container.current?.querySelectorAll<HTMLElement>(SELECTOR) ?? [])
      .filter((element) => element.getClientRects().length > 0 && !element.closest('[hidden], [inert]'))
    const firstInput = container.current?.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled]), select:not([disabled])')
    ;(firstInput ?? focusables()[0] ?? container.current)?.focus()

    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        if (!latest.current.busy) latest.current.onClose()
      }
      if (event.key !== 'Tab') return
      const targets = focusables()
      const first = targets[0]
      const last = targets[targets.length - 1]
      if (!first || !last) { event.preventDefault(); container.current?.focus(); return }
      if (!container.current?.contains(document.activeElement) || (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault(); first.focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus()
      }
    }
    document.addEventListener('keydown', keydown, true)
    return () => {
      document.removeEventListener('keydown', keydown, true)
      document.body.style.overflow = overflow
      if (previous?.isConnected && !previous.closest('[inert]')) previous.focus()
    }
  }, [open, container])
}
