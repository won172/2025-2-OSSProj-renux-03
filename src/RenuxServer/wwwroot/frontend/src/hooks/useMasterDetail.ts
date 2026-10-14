import { useCallback, useEffect, useRef, useState } from 'react'

/** Same content on desktop; mobile swaps the list for the selected detail/form. */
export function useMasterDetail() {
  const [shown, setShown] = useState(false)
  const [request, setRequest] = useState(0)
  const detailRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const shownRef = useRef(false)
  const returnFrame = useRef<number | null>(null)
  const opener = useRef<HTMLElement | null>(null)
  const focusInput = useRef(false)

  const reveal = useCallback((form = false) => {
    if (!shownRef.current) opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    shownRef.current = true
    focusInput.current = form
    setShown(true)
    setRequest((value) => value + 1)
  }, [])

  useEffect(() => {
    if (!shown || !request) return
    const target = focusInput.current
      ? detailRef.current?.querySelector<HTMLElement>('input:not([disabled]), select:not([disabled]), textarea:not([disabled])')
      : detailRef.current?.querySelector<HTMLElement>('h2')
    ;(target ?? detailRef.current)?.focus({ preventScroll: true })
    if (window.matchMedia('(max-width: 960px)').matches) {
      detailRef.current?.scrollIntoView({ block: 'start' })
    }
  }, [shown, request])

  useEffect(() => () => {
    if (returnFrame.current !== null) window.cancelAnimationFrame(returnFrame.current)
  }, [])

  const back = () => {
    shownRef.current = false
    setShown(false)
    returnFrame.current = window.requestAnimationFrame(() => {
      returnFrame.current = null
      if (shownRef.current) return
      const visible = (element: HTMLElement | null) => element?.isConnected
        && !element.closest('[hidden], [inert]') && element.getClientRects().length > 0
      const target = visible(opener.current) ? opener.current : visible(listRef.current) ? listRef.current : null
      target?.focus()
    })
  }
  return { shown, detailRef, listRef, reveal, back }
}
