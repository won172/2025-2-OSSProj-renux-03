import { useCallback, useEffect, useRef, useState } from 'react'
import {
  DEFAULT_REMINDER,
  createPersonalEvent,
  fetchSavedEvents,
  saveEvent,
  savedErrorMessage,
  unsaveEvent,
  updatePersonalEvent,
  type PersonalEventInput,
  type SavedEvent,
  type SavedReminder,
} from './savedEventsApi.ts'

export type SavedStatus = 'loading' | 'guest' | 'ready' | 'error'

/** 저장한 일정 목록과 저장·해제·알림 설정. 같은 일정에 대한 요청은 하나씩만 보낸다. */
export const useSavedEvents = () => {
  const [status, setStatus] = useState<SavedStatus>('loading')
  const [byId, setById] = useState<Map<string, SavedEvent>>(() => new Map())
  const [pending, setPending] = useState<Set<string>>(() => new Set())
  const [message, setMessage] = useState<string | null>(null)
  const pendingRef = useRef(new Set<string>())
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    fetchSavedEvents(controller.signal).then(
      (result) => {
        if (!result.signedIn) { setStatus('guest'); return }
        setById(new Map(result.events.map((event) => [event.eventId, event])))
        setStatus('ready')
      },
      () => { if (!controller.signal.aborted) setStatus('error') },
    )
    return () => { mounted.current = false; controller.abort() }
  }, [])

  const track = useCallback(async (eventId: string, run: () => Promise<void>) => {
    if (pendingRef.current.has(eventId)) return
    pendingRef.current.add(eventId)
    setPending(new Set(pendingRef.current))
    try {
      await run()
    } finally {
      pendingRef.current.delete(eventId)
      if (mounted.current) setPending(new Set(pendingRef.current))
    }
  }, [])

  const put = (event: SavedEvent) => setById((previous) => new Map(previous).set(event.eventId, event))
  const drop = (eventId: string) => setById((previous) => {
    const next = new Map(previous)
    next.delete(eventId)
    return next
  })

  /** 저장은 서버가 공식 일정을 확인한 뒤 반영하고, 해제는 바로 반영한 뒤 실패하면 되돌린다. */
  const toggle = useCallback((event: { id: string; endDate: string }) => track(event.id, async () => {
    const existing = byId.get(event.id)
    setMessage(null)
    try {
      if (existing) {
        drop(event.id)
        await unsaveEvent(event.id)
      } else {
        put(await saveEvent(event, DEFAULT_REMINDER))
      }
    } catch (error) {
      if (!mounted.current) return
      if (existing) put(existing)
      setMessage(savedErrorMessage(error))
    }
  }), [byId, track])

  const setReminder = useCallback((eventId: string, reminder: SavedReminder) => track(eventId, async () => {
    const existing = byId.get(eventId)
    if (!existing) return
    setMessage(null)
    put({ ...existing, reminder })
    try {
      put(await saveEvent({ id: eventId, endDate: existing.endDate }, reminder))
    } catch (error) {
      if (!mounted.current) return
      put(existing)
      setMessage(savedErrorMessage(error))
    }
  }), [byId, track])

  /** 개인 일정 만들기·고치기. 성공하면 저장된 일정을, 실패하면 null을 돌려주고 안내를 띄운다. */
  const savePersonal = useCallback(async (input: PersonalEventInput, eventId?: string): Promise<SavedEvent | null> => {
    setMessage(null)
    try {
      const saved = eventId ? await updatePersonalEvent(eventId, input) : await createPersonalEvent(input)
      if (mounted.current) put(saved)
      return saved
    } catch (error) {
      if (mounted.current) setMessage(savedErrorMessage(error))
      return null
    }
  }, [])

  /** 개인 일정 삭제. 바로 반영하고 실패하면 되돌린다. */
  const remove = useCallback((eventId: string) => track(eventId, async () => {
    const existing = byId.get(eventId)
    if (!existing) return
    setMessage(null)
    drop(eventId)
    try {
      await unsaveEvent(eventId)
    } catch (error) {
      if (!mounted.current) return
      put(existing)
      setMessage(savedErrorMessage(error))
    }
  }), [byId, track])

  return { status, byId, pending, message, clearMessage: useCallback(() => setMessage(null), []), toggle, setReminder, savePersonal, remove }
}
