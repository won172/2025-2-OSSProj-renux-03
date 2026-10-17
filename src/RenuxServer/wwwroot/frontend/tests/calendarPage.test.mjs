import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { createServer } from 'vite'
import { testDocument, click, settle, button, change, props } from './domHarness.mjs'
import { kstToday, monthGrid, monthOf, shiftMonth } from '../src/pages/calendar/calendarDates.ts'

// 상태·DOM 구조만 확인한다. CSS 배치(겹침·가로 넘침)는 브라우저 스크린샷으로 따로 본다.
const serverForTest = () => createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })

// requests: GET /calendar/events만. savedRequests: /calendar/saved 요청(method·body 포함).
// saved를 주지 않으면 로그인하지 않은 상태(401)다.
async function withCalendar(respond, run, { desktop = false, saved = () => json({}, 401) } = {}) {
  const server = await serverForTest()
  const dom = testDocument()
  dom.window.matchMedia = () => ({ matches: desktop, addEventListener() {}, removeEventListener() {} })
  const originalFetch = globalThis.fetch
  const requests = []
  const savedRequests = []
  let root
  let router
  try {
    const { default: CalendarPage } = await server.ssrLoadModule('/src/pages/calendar/CalendarPage.tsx')
    globalThis.fetch = async (input, init = {}) => {
      const url = new URL(input, 'http://localhost')
      if (url.pathname.startsWith('/calendar/saved')) {
        const request = { url, method: init.method ?? 'GET', body: init.body ? JSON.parse(init.body) : null }
        savedRequests.push(request)
        return saved(request, savedRequests.length)
      }
      requests.push(url)
      return respond(url, requests.length)
    }
    router = createMemoryRouter([
      { path: '/calendar/*', element: React.createElement(CalendarPage) },
      { path: '/chat', element: React.createElement('p', null, 'CHAT') },
    ], { initialEntries: ['/calendar'] })
    root = createRoot(dom.container)
    flushSync(() => root.render(React.createElement(RouterProvider, { router })))
    await settle()
    await run({ dom, router, requests, savedRequests })
  } finally {
    globalThis.fetch = originalFetch
    if (root) flushSync(() => root.unmount())
    router?.dispose()
    await new Promise((resolve) => setTimeout(resolve, 20))
    dom.restore()
    await server.close()
  }
}

const json = (value, status = 200) => new Response(status === 204 ? null : JSON.stringify(value), { status })
const byLabel = (container, label) => Array.from(container.getElementsByTagName('button')).find((node) => node.getAttribute('aria-label') === label)
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
    assert.ok(button(detail, '일정 저장'), 'guests see the save button; saving asks them to log in (covered below)')
    assert.doesNotMatch(detail.textContent, /로그인하면/, 'no standing login paragraph in the detail')
    click(byLabel(dom.container, '이 일정 질문하기'))
    await settle()
    assert.equal(router.state.location.pathname, '/chat')
    assert.equal(router.state.location.state.prefillQuestion, '중간고사 시간표 일정 자세히 알려줘')
  })
})

const savedRow = (overrides) => ({ eventId: 'c1', source: 'schedule', kind: 'academic', title: '수강 정정', startDate: null, endDate: today, url: null, reminder: { enabled: true, daysBefore: [7, 1] }, createdTime: 'now', updatedTime: 'now', ...overrides })
const calendarJson = (list) => (url) => json({ from: url.searchParams.get('from'), to: url.searchParams.get('to'), generatedAt: 'now', events: list })
const star = (dom, title, saved) => byLabel(dom.container, `${title} ${saved ? '저장 해제' : '저장'}`)

test('signed-in: saved events show a pressed star, the 저장됨 chip filters, and unsaving deletes (rolled back on failure)', async () => {
  const more = [...events, row({ id: 'c2', title: '복학 신청' })]
  let deleteStatus = 500
  await withCalendar(calendarJson(more), async ({ dom, savedRequests }) => {
    assert.equal(savedRequests[0].method, 'GET')
    const savedStar = star(dom, '수강 정정', true)
    assert.ok(savedStar, 'saved event has an unsave star')
    assert.equal(savedStar.getAttribute('aria-pressed'), 'true')
    assert.equal(star(dom, '복학 신청', false).getAttribute('aria-pressed'), 'false')
    assert.equal(dom.container.querySelectorAll('.cal-unverified .cal-star').length, 0, 'unverified events cannot be saved')

    const chip = button(dom.container, '저장됨')
    assert.equal(chip.getAttribute('aria-pressed'), 'false')
    click(chip)
    assert.deepEqual(Array.from(dom.container.querySelectorAll('.cal-card__title')).map((node) => node.textContent), ['수강 정정'])
    assert.equal(dom.container.querySelector('.cal-unverified'), null, 'saved-only view hides unverified events')
    click(button(dom.container, '저장됨'))

    click(star(dom, '수강 정정', true))
    await settle()
    const removal = savedRequests.at(-1)
    assert.equal(removal.method, 'DELETE')
    assert.equal(removal.url.searchParams.get('eventId'), 'c1')
    assert.ok(star(dom, '수강 정정', true), 'a failed unsave restores the star')
    assert.match(dom.container.querySelector('.cal-notice').textContent, /저장하지 못했어요\. \(오류 500\)/)

    deleteStatus = 204
    click(star(dom, '수강 정정', true))
    await settle()
    assert.ok(star(dom, '수강 정정', false), 'a successful unsave clears the star')
    assert.equal(dom.container.querySelector('.cal-notice'), null)
  }, { saved: (request) => request.method === 'GET' ? json([savedRow()]) : json({}, deleteStatus) })
})

test('detail: saving uses the official event id and date, then the reminder card edits 7일 전 · 1일 전 · 당일', async () => {
  const event = row({ id: 'notices:9', source: 'notices', kind: 'notice', title: '국가장학금 신청', startDate: addDaysLocal(today, 3), endDate: addDaysLocal(today, 9), url: 'https://www.dongguk.edu/n/9' })
  let reminder = { enabled: true, daysBefore: [7, 1] }
  await withCalendar(calendarJson([event]), async ({ dom, router, savedRequests }) => {
    await router.navigate(`/calendar/event/${encodeURIComponent(event.id)}?d=${event.startDate}`, { state: { event } })
    await settle()
    const detail = dom.container.querySelector('.cal-detail')
    const save = button(detail, '일정 저장')
    assert.equal(save.getAttribute('aria-pressed'), 'false')
    click(save)
    await settle()
    const put = savedRequests.at(-1)
    assert.equal(put.method, 'PUT')
    assert.deepEqual(put.body, { eventId: 'notices:9', endDate: event.endDate, reminder: { enabled: true, daysBefore: [7, 1] } })
    assert.equal(button(dom.container, '저장됨').getAttribute('aria-pressed'), 'true')

    const toggle = dom.container.querySelector('button[role=switch]')
    assert.equal(toggle.getAttribute('aria-checked'), 'true')
    const pills = Array.from(dom.container.querySelectorAll('.cal-pill'))
    assert.deepEqual(pills.map((node) => [node.textContent, node.getAttribute('aria-pressed')]), [['7일 전', 'true'], ['1일 전', 'true'], ['당일', 'false']])
    click(pills[2])
    await settle()
    assert.deepEqual(savedRequests.at(-1).body.reminder, { enabled: true, daysBefore: [7, 1, 0] })
    assert.equal(Array.from(dom.container.querySelectorAll('.cal-pill'))[2].getAttribute('aria-pressed'), 'true')

    click(dom.container.querySelector('button[role=switch]'))
    await settle()
    assert.deepEqual(savedRequests.at(-1).body.reminder, { enabled: false, daysBefore: [7, 1, 0] })
    assert.equal(dom.container.querySelector('button[role=switch]').getAttribute('aria-checked'), 'false')
    assert.equal(dom.container.querySelectorAll('.cal-pill').length, 0, 'steps hide while reminders are off')
  }, {
    saved: (request) => {
      if (request.method === 'GET') return json([])
      reminder = request.body.reminder
      return json(savedRow({ eventId: request.body.eventId, source: 'notices', kind: 'notice', title: '국가장학금 신청', startDate: addDaysLocal(today, 3), endDate: request.body.endDate, reminder }), 201)
    },
  })
})

test('a refused save (date_unverified) explains why and leaves the event unsaved', async () => {
  const event = row({ id: 'c9', title: '확인 중인 일정' })
  await withCalendar(calendarJson([event]), async ({ dom, router }) => {
    await router.navigate('/calendar/event/c9', { state: { event } })
    await settle()
    click(button(dom.container.querySelector('.cal-detail'), '일정 저장'))
    await settle()
    assert.match(dom.container.querySelector('.cal-notice').textContent, /날짜를 확실히 읽지 못한 일정이라 저장할 수 없어요/)
    assert.ok(button(dom.container.querySelector('.cal-detail'), '일정 저장'))
  }, { saved: (request) => request.method === 'GET' ? json([]) : json({ error: 'date_unverified', message: 'x' }, 409) })
})

test('guests see stars but saving asks them to log in instead of calling the API', async () => {
  await withCalendar(calendarJson(events), async ({ dom, savedRequests }) => {
    assert.equal(button(dom.container, '저장됨'), undefined, 'guests have no saved filter')
    click(star(dom, '수강 정정', false))
    await settle()
    const notice = dom.container.querySelector('.cal-notice')
    assert.match(notice.textContent, /로그인하면 일정을 저장하고/)
    assert.equal(notice.getElementsByTagName('a')[0].getAttribute('href'), '/auth/in')
    assert.deepEqual(savedRequests.map((request) => request.method), ['GET'], 'no save request for guests')
    click(byLabel(dom.container, '안내 닫기'))
    assert.equal(dom.container.querySelector('.cal-notice'), null)
  })
})

test('unverified detail has no save button, only 질문', async () => {
  const event = row({ id: 'u9', title: '날짜 애매', dateStatus: 'unverified' })
  await withCalendar(calendarJson([event]), async ({ dom, router }) => {
    await router.navigate('/calendar/event/u9', { state: { event } })
    await settle()
    const detail = dom.container.querySelector('.cal-detail')
    assert.equal(button(detail, '일정 저장'), undefined)
    assert.ok(byLabel(detail, '이 일정 질문하기'))
  }, { saved: (request) => json([]) })
})

function addDaysLocal(ymd, days) {
  const date = new Date(`${ymd}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

const personalRow = (overrides) => savedRow({ eventId: `personal:${'a'.repeat(32)}`, source: 'personal', kind: 'personal', title: '동아리 회의', startDate: null, endDate: today, url: null, ...overrides })
const textOf = (node) => node.textContent
// 테스트 DOM은 하위 선택자를 지원하지 않으므로 폼 안의 input을 직접 찾는다.
const formInputs = (dom) => Array.from(dom.container.querySelector('form.cal-form').getElementsByTagName('input'))
const textInputs = (dom) => formInputs(dom).filter((node) => props(node).type !== 'checkbox')

test('signed-in: personal events show on the calendar with their own tag and chip, no star, and edit/delete instead of save', async () => {
  let deleted = null
  await withCalendar(calendarJson(events), async ({ dom, router, savedRequests }) => {
    const titles = Array.from(dom.container.querySelectorAll('.cal-card__title')).map(textOf)
    assert.ok(titles.includes('동아리 회의'), titles.join(','))
    const item = Array.from(dom.container.querySelectorAll('.cal-item')).find((node) => node.textContent.includes('동아리 회의'))
    assert.match(item.textContent, /내 일정/)
    assert.equal(item.querySelector('.cal-star'), null, 'personal events have no save star')
    assert.doesNotMatch(item.textContent, /마감/, 'personal events are not called deadlines')
    const todayCell = cellFor(dom, `${Number(today.slice(5, 7))}월 ${Number(today.slice(8, 10))}일 오늘`)
    assert.equal(todayCell.querySelectorAll('.cal-dot--personal').length, 1, 'personal events get their own dot on the month grid')
    const chip = button(dom.container, '내 일정')
    assert.equal(chip.getAttribute('aria-pressed'), 'true')
    click(chip)
    assert.ok(!Array.from(dom.container.querySelectorAll('.cal-card__title')).map(textOf).includes('동아리 회의'), 'the chip hides personal events')
    click(button(dom.container, '내 일정'))

    await router.navigate(`/calendar/event/${encodeURIComponent(personalRow().eventId)}`)
    await settle()
    const detail = dom.container.querySelector('.cal-detail')
    assert.doesNotMatch(detail.textContent, /근거|원문/, 'personal events have no evidence card')
    assert.equal(button(detail, '일정 저장'), undefined)
    assert.ok(button(detail, '수정'))
    click(button(detail, '삭제'))
    assert.ok(button(dom.container, '한 번 더 누르면 삭제'), 'deleting needs a second tap')
    assert.equal(savedRequests.filter((request) => request.method === 'DELETE').length, 0)
    click(button(dom.container, '한 번 더 누르면 삭제'))
    await settle()
    assert.equal(deleted, personalRow().eventId)
    assert.equal(router.state.location.pathname, '/calendar')
    assert.ok(!Array.from(dom.container.querySelectorAll('.cal-card__title')).map(textOf).includes('동아리 회의'))
  }, {
    saved: (request) => {
      if (request.method === 'DELETE') { deleted = request.url.searchParams.get('eventId'); return json(null, 204) }
      return json([personalRow()])
    },
  })
})

test('adding a personal event validates the title, posts the form and opens the new event', async () => {
  const created = personalRow({ eventId: `personal:${'b'.repeat(32)}`, title: '과제 제출', endDate: addDaysLocal(today, 2) })
  await withCalendar(calendarJson([]), async ({ dom, router, savedRequests }) => {
    click(byLabel(dom.container, '내 일정 추가'))
    await settle()
    assert.equal(router.state.location.pathname, '/calendar/new')
    const form = dom.container.querySelector('form.cal-form')
    assert.ok(form)
    props(form).onSubmit({ preventDefault() {} })
    await settle()
    assert.match(dom.container.querySelector('.cal-form__error').textContent, /제목을 입력해 주세요/)
    assert.equal(savedRequests.filter((request) => request.method === 'POST').length, 0, 'no request until the form is valid')

    const [titleInput, dateInput] = textInputs(dom)
    change(titleInput, '  과제 제출  ')
    change(dateInput, created.endDate)
    props(dom.container.querySelector('form.cal-form')).onSubmit({ preventDefault() {} })
    await settle()
    const post = savedRequests.find((request) => request.method === 'POST')
    assert.equal(post.url.pathname, '/calendar/saved/personal')
    assert.deepEqual(post.body, { title: '과제 제출', startDate: null, endDate: created.endDate, reminder: { enabled: true, daysBefore: [7, 1] } })
    assert.equal(router.state.location.pathname, `/calendar/event/${encodeURIComponent(created.eventId)}`)
    assert.match(dom.container.querySelector('.cal-detail').textContent, /과제 제출/)
  }, { saved: (request) => request.method === 'POST' ? json(created, 201) : json([]) })
})

test('editing a personal event prefills the form and puts a multi-day period', async () => {
  const existing = personalRow()
  await withCalendar(calendarJson([]), async ({ dom, router, savedRequests }) => {
    await router.navigate(`/calendar/event/${encodeURIComponent(existing.eventId)}/edit`)
    await settle()
    const titleInput = textInputs(dom)[0]
    assert.equal(titleInput.getAttribute('value') ?? titleInput.value, '동아리 회의')
    const multi = formInputs(dom).find((node) => props(node).type === 'checkbox')
    props(multi).onChange({ target: { checked: true } })
    await settle()
    const inputs = textInputs(dom)
    assert.equal(inputs.length, 3, 'a 종료일 field appears')
    change(inputs[2], addDaysLocal(today, 3))
    props(dom.container.querySelector('form.cal-form')).onSubmit({ preventDefault() {} })
    await settle()
    const put = savedRequests.find((request) => request.method === 'PUT')
    assert.equal(put.url.pathname, '/calendar/saved/personal')
    assert.deepEqual(put.body, { eventId: existing.eventId, title: '동아리 회의', startDate: today, endDate: addDaysLocal(today, 3), reminder: { enabled: true, daysBefore: [7, 1] } })
  }, { saved: (request) => request.method === 'PUT' ? json(personalRow({ startDate: request.body.startDate, endDate: request.body.endDate }), 200) : json([existing]) })
})

test('guests asked to log in when adding a personal event', async () => {
  await withCalendar(calendarJson(events), async ({ dom, router, savedRequests }) => {
    assert.equal(button(dom.container, '내 일정'), undefined, 'no personal chip for guests')
    click(byLabel(dom.container, '내 일정 추가'))
    await settle()
    assert.equal(router.state.location.pathname, '/calendar')
    assert.match(dom.container.querySelector('.cal-notice').textContent, /로그인하면/)
    assert.deepEqual(savedRequests.map((request) => request.method), ['GET'])
  })
})
