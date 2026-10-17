import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { createServer } from 'vite'
import { testDocument, click, settle, button } from './domHarness.mjs'
import { kstToday, monthGrid, monthOf, shiftMonth } from '../src/pages/calendar/calendarDates.ts'

// 상태·DOM 구조만 확인한다. CSS 배치(겹침·가로 넘침)는 브라우저 스크린샷으로 따로 본다.
const serverForTest = () => createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })

async function withCalendar(respond, run, { desktop = false } = {}) {
  const server = await serverForTest()
  const dom = testDocument()
  dom.window.matchMedia = () => ({ matches: desktop, addEventListener() {}, removeEventListener() {} })
  const originalFetch = globalThis.fetch
  const requests = []
  let root
  let router
  try {
    const { default: CalendarPage } = await server.ssrLoadModule('/src/pages/calendar/CalendarPage.tsx')
    globalThis.fetch = async (input) => {
      const url = new URL(input, 'http://localhost')
      requests.push(url)
      return respond(url, requests.length)
    }
    router = createMemoryRouter([
      { path: '/calendar/*', element: React.createElement(CalendarPage) },
      { path: '/', element: React.createElement('p', null, 'HOME') },
    ], { initialEntries: ['/calendar'] })
    root = createRoot(dom.container)
    flushSync(() => root.render(React.createElement(RouterProvider, { router })))
    await settle()
    await run({ dom, router, requests })
  } finally {
    globalThis.fetch = originalFetch
    if (root) flushSync(() => root.unmount())
    router?.dispose()
    await new Promise((resolve) => setTimeout(resolve, 20))
    dom.restore()
    await server.close()
  }
}

const json = (value, status = 200) => new Response(JSON.stringify(value), { status })
const cellFor = (dom, label) => Array.from(dom.container.querySelectorAll('button.cal-cell')).find((node) => node.getAttribute('aria-label').startsWith(label))

const today = kstToday()
const row = (overrides) => ({ source: 'schedule', kind: 'academic', startDate: null, endDate: today, url: null, snippet: null, evidence: null, dateStatus: 'confirmed', department: null, publishedAt: null, ...overrides })
const events = [
  row({ id: 'c1', title: '수강 정정' }),
  row({ id: 'u1', source: 'notices', kind: 'notice', title: '장학 신청 안내', url: 'https://www.dongguk.edu/n/1', dateStatus: 'unverified', department: '장학팀' }),
]

test('month view fetches only the visible grid and caches months already loaded', async () => {
  await withCalendar((url) => json({ from: url.searchParams.get('from'), to: url.searchParams.get('to'), generatedAt: 'now', events }), async ({ dom, requests }) => {
    const grid = monthGrid(monthOf(today))
    assert.equal(requests.length, 1)
    assert.equal(requests[0].pathname, '/calendar/events')
    assert.equal(requests[0].searchParams.get('from'), grid.from)
    assert.equal(requests[0].searchParams.get('to'), grid.to)
    click(dom.container.querySelector('[aria-label="다음 달"]'))
    await settle()
    assert.equal(requests.length, 2)
    const next = monthGrid(shiftMonth(monthOf(today), 1))
    assert.equal(requests[1].searchParams.get('from'), next.from)
    click(dom.container.querySelector('[aria-label="이전 달"]'))
    await settle()
    assert.equal(requests.length, 2, 'the already-loaded month is not refetched')
  })
})

test('unverified events stay off the grid and are listed separately; chips toggle with aria-pressed', async () => {
  await withCalendar((url) => json({ from: url.searchParams.get('from'), to: url.searchParams.get('to'), generatedAt: 'now', events }), async ({ dom }) => {
    const label = `${Number(today.slice(5, 7))}월 ${Number(today.slice(8, 10))}일 오늘`
    const cell = cellFor(dom, label)
    assert.ok(cell, 'today cell has an aria-label with the date')
    assert.equal(cell.getAttribute('aria-label'), `${label}, 일정 1개`, 'unverified events are not counted on the grid')
    assert.equal(cell.getAttribute('aria-current'), 'date')
    assert.equal(cell.getAttribute('aria-pressed'), 'true')
    assert.equal(cell.querySelectorAll('.cal-dot').length, 1)
    const cards = dom.container.querySelectorAll('.cal-card')
    assert.equal(cards.length, 1)
    assert.match(cards[0].textContent, /학사오늘 마감수강 정정/)
    const unverified = dom.container.querySelector('.cal-unverified')
    assert.match(unverified.textContent, /날짜 확인 필요 — 공지에서 날짜를 확실히 읽지 못했어요\. 캘린더에는 넣지 않았어요\./)
    assert.equal(unverified.querySelector('a').getAttribute('href'), 'https://www.dongguk.edu/n/1')
    const chip = button(dom.container, '학사')
    assert.equal(chip.getAttribute('aria-pressed'), 'true')
    click(chip)
    assert.equal(button(dom.container, '학사').getAttribute('aria-pressed'), 'false')
    assert.match(dom.container.textContent, /이 날은 일정이 없어요/)
  })
})

test('a 503 shows an error with retry; retry refetches the same range', async () => {
  await withCalendar((url, count) => count === 1
    ? json({ error: 'upstream' }, 503)
    : json({ from: url.searchParams.get('from'), to: url.searchParams.get('to'), generatedAt: 'now', events: [] }), async ({ dom, requests }) => {
    const alert = dom.container.querySelector('[role="alert"]')
    assert.ok(alert)
    assert.match(alert.textContent, /잠시 불러올 수 없어요/)
    assert.doesNotMatch(dom.container.textContent, /이 날은 일정이 없어요/, 'an error is not shown as an empty day')
    click(button(dom.container, '다시 시도'))
    await settle()
    assert.equal(requests.length, 2)
    assert.equal(requests[1].search, requests[0].search)
    assert.equal(dom.container.querySelector('[role="alert"]'), null)
    assert.match(dom.container.textContent, /이 날은 일정이 없어요/)
  })
})

test('detail route shows evidence and source, and 질문 opens chat with a prefilled question', async () => {
  const event = { id: 'n/7', source: 'notices', kind: 'exam', title: '중간고사 시간표', startDate: '2026-10-19', endDate: '2026-10-23', url: 'https://www.dongguk.edu/n/7', snippet: '요약', evidence: '중간고사는 10월 19일부터 23일까지', dateStatus: 'confirmed', department: '학사지원팀', publishedAt: null }
  await withCalendar((url) => json({ from: url.searchParams.get('from'), to: url.searchParams.get('to'), generatedAt: 'now', events: [event] }), async ({ dom, router }) => {
    await router.navigate('/calendar/event/n%2F7?d=2026-10-19', { state: { event } })
    await settle()
    const detail = dom.container.querySelector('.cal-detail')
    assert.ok(detail, dom.container.textContent)
    assert.match(detail.textContent, /시험중간고사 시간표/)
    assert.match(detail.textContent, /10\.19\(월\) – 10\.23\(금\)/)
    assert.match(detail.textContent, /주관\/부서 · 학사지원팀/)
    assert.equal(detail.querySelector('blockquote').textContent, '중간고사는 10월 19일부터 23일까지')
    const source = Array.from(detail.getElementsByTagName('a')).find((node) => node.textContent.includes('원문 보기'))
    assert.equal(source.getAttribute('href'), 'https://www.dongguk.edu/n/7')
    assert.equal(source.getAttribute('target'), '_blank')
    assert.equal(detail.textContent.includes('저장'), false, 'no save/star control yet')
    click(button(dom.container, '이 일정 질문하기'))
    await settle()
    assert.equal(router.state.location.pathname, '/')
    assert.equal(router.state.location.state.prefillQuestion, '중간고사 시간표 일정 자세히 알려줘')
  })
})
