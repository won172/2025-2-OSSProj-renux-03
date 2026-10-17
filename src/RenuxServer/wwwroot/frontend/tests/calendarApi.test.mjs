import assert from 'node:assert/strict'
import test from 'node:test'

import { Capacitor } from '@capacitor/core'

import {
  MAX_RANGE_DAYS,
  buildCalendarQuestion,
  calendarErrorMessage,
  fetchCalendarEvents,
  normalizeEvent,
  splitByDateStatus,
  validateRange,
} from '../src/pages/calendar/calendarApi.ts'

const raw = (overrides = {}) => ({
  id: 'schedule:1',
  source: 'schedule',
  kind: 'academic',
  title: '2학기 중간고사 기간',
  startDate: '2026-10-19',
  endDate: '2026-10-23',
  url: 'https://www.dongguk.edu/schedule/1',
  snippet: '중간고사',
  evidence: null,
  dateStatus: 'confirmed',
  department: null,
  publishedAt: null,
  ...overrides,
})

test('validateRange enforces the 92-day contract before calling the API', () => {
  assert.equal(MAX_RANGE_DAYS, 92)
  assert.equal(validateRange('2026-10-01', '2026-12-31'), null) // 92일
  assert.match(validateRange('2026-10-01', '2027-01-01'), /92일/) // 93일
  assert.match(validateRange('2026-10-02', '2026-10-01'), /늦습니다/)
  assert.match(validateRange('2026-10-1', '2026-10-31'), /형식/)
  assert.equal(validateRange('2026-10-01', '2026-10-01'), null)
})

test('fetchCalendarEvents rejects an invalid range locally with status 400 and makes no request', async (t) => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => { throw new Error('must not be called') })
  await assert.rejects(fetchCalendarEvents('2026-10-01', '2027-01-15'), (error) => error.status === 400)
  assert.equal(fetchMock.mock.callCount(), 0)
})

test('fetchCalendarEvents calls GET /calendar/events with from/to, normalizes and sorts events', async (t) => {
  t.mock.method(Capacitor, 'isNativePlatform', () => false)
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({
    from: '2026-09-27',
    to: '2026-10-31',
    generatedAt: '2026-10-16T00:00:00Z',
    events: [
      raw({ id: 'b', startDate: null, endDate: '2026-10-21', kind: 'notice', source: 'notices', department: '학사지원팀' }),
      raw({ id: 'a' }),
      raw({ id: 'bad-kind', kind: 'party' }),
      raw({ id: 'bad-date', endDate: '2026/10/21' }),
      raw({ id: 'js-url', url: 'javascript:alert(1)' }),
      null,
    ],
  }), { status: 200 }))
  const controller = new AbortController()
  const result = await fetchCalendarEvents('2026-09-27', '2026-10-31', controller.signal)
  const [url, init] = fetchMock.mock.calls[0].arguments
  assert.equal(url, '/calendar/events?from=2026-09-27&to=2026-10-31')
  assert.equal(init.method, 'GET')
  assert.equal(init.signal, controller.signal)
  assert.equal(init.credentials, 'include')
  assert.deepEqual(result.events.map((event) => event.id), ['a', 'js-url', 'b'])
  assert.equal(result.events.find((event) => event.id === 'js-url').url, null, 'non-http links are dropped')
})

test('API failures keep their status for the retry message (400 / 503 / network)', async (t) => {
  t.mock.method(Capacitor, 'isNativePlatform', () => false)
  t.mock.method(globalThis, 'fetch', async () => new Response('{"error":"upstream"}', { status: 503 }))
  const error = await fetchCalendarEvents('2026-10-01', '2026-10-31').catch((caught) => caught)
  assert.equal(error.status, 503)
  assert.match(calendarErrorMessage(error), /잠시 불러올 수 없어요/)
  assert.match(calendarErrorMessage({ status: 400 }), /조회 기간/)
  assert.match(calendarErrorMessage({ status: 429 }), /잠시 후 다시 시도/)
  assert.match(calendarErrorMessage(new TypeError('Failed to fetch')), /네트워크/)
})

test('unverified events are split out and never treated as confirmed', () => {
  const events = [
    normalizeEvent(raw({ id: 'ok' })),
    normalizeEvent(raw({ id: 'unverified', dateStatus: 'unverified' })),
    normalizeEvent(raw({ id: 'unknown-status', dateStatus: 'maybe' })),
  ]
  const { confirmed, unverified } = splitByDateStatus(events)
  assert.deepEqual(confirmed.map((event) => event.id), ['ok'])
  assert.deepEqual(unverified.map((event) => event.id), ['unverified', 'unknown-status'])
})

test('normalizeEvent treats an out-of-order startDate as a single-day item and builds the chat question', () => {
  const event = normalizeEvent(raw({ startDate: '2026-10-30', endDate: '2026-10-23' }))
  assert.equal(event.startDate, null)
  assert.equal(buildCalendarQuestion(event), '2학기 중간고사 기간 일정 자세히 알려줘')
})
