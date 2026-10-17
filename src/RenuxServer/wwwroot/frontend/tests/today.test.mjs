import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { createServer } from 'vite'
import { testDocument, change, settle, props } from './domHarness.mjs'
import { addDays, kstToday } from '../src/pages/calendar/calendarDates.ts'
import {
  briefingNotices,
  eventDetailPath,
  nextSevenDays,
  restaurantsFromBriefing,
  todayLabel,
  upcomingEvents,
} from '../src/pages/today/todayData.ts'

const today = kstToday()
const row = (overrides) => ({ source: 'schedule', kind: 'academic', startDate: null, endDate: today, url: null, snippet: null, evidence: null, dateStatus: 'confirmed', department: null, publishedAt: null, ...overrides })

test('todayLabel shows month, day and Korean weekday', () => {
  assert.equal(todayLabel('2026-10-17'), '10월 17일 토요일')
  assert.equal(todayLabel('2026-01-05'), '1월 5일 월요일')
})

test('upcoming events: confirmed and not ended, ordered by next start or deadline, limited', () => {
  const t = '2026-10-17'
  const events = [
    row({ id: 'ended', title: '지난 일정', endDate: '2026-10-16' }),
    row({ id: 'unverified', title: '확인 필요', endDate: '2026-10-18', dateStatus: 'unverified' }),
    row({ id: 'ongoing', title: '진행 중 기간', startDate: '2026-10-01', endDate: '2026-10-30' }),
    row({ id: 'starts', title: '다음 주 시작', startDate: '2026-10-20', endDate: '2026-10-24' }),
    row({ id: 'deadline', title: '오늘 마감', endDate: '2026-10-17' }),
  ]
  assert.deepEqual(upcomingEvents(events, t, 10).map((event) => event.id), ['deadline', 'starts', 'ongoing'])
  assert.deepEqual(upcomingEvents(events, t, 2).map((event) => event.id), ['deadline', 'starts'])
})

test('restaurants: grouped list wins; old responses fall back to the representative corners', () => {
  const corners = [{ corner: '중식 A', menu: '치킨커틀렛' }]
  assert.deepEqual(restaurantsFromBriefing({ meals: [{ corner: '학생식당 중식', menu: '김치찌개' }], restaurants: [{ name: ' 경영관 ', corners }, { name: '빈 식당', corners: [] }], schedules: [], notices: [] }),
    [{ name: '경영관', corners }])
  assert.deepEqual(restaurantsFromBriefing({ meals: [{ corner: '학생식당 중식', menu: '김치찌개' }], schedules: [], notices: [] }),
    [{ name: '오늘 학식', corners: [{ corner: '학생식당 중식', menu: '김치찌개' }] }])
  assert.deepEqual(restaurantsFromBriefing({ meals: [], schedules: [], notices: [] }), [])
  assert.deepEqual(restaurantsFromBriefing(null), [])
})

test('briefing notices keep only http(s) links and drop untitled rows', () => {
  const notices = briefingNotices({ meals: [], schedules: [], notices: [
    { title: ' 장학 안내 ', url: 'https://www.dongguk.edu/a/1', board: '장학공지' },
    { title: '스크립트 링크', url: 'javascript:alert(1)' },
    { title: '  ', url: 'https://www.dongguk.edu/a/2' },
  ] })
  assert.deepEqual(notices.map(({ title, url, board }) => [title, url, board]), [['장학 안내', 'https://www.dongguk.edu/a/1', '장학공지'], ['스크립트 링크', null, null]])
})

test('next seven days start today and only hold confirmed events that occur that day', () => {
  const t = '2026-10-17'
  const week = nextSevenDays([
    row({ id: 'span', title: '기간', startDate: '2026-10-16', endDate: '2026-10-19' }),
    row({ id: 'later', title: '나중', endDate: '2026-10-23' }),
    row({ id: 'unverified', title: '확인 필요', endDate: '2026-10-17', dateStatus: 'unverified' }),
  ], t)
  assert.deepEqual(week.map((day) => day.date), Array.from({ length: 7 }, (_, index) => addDays(t, index)))
  assert.equal(week[0].label, '10.17 토')
  assert.equal(week[0].isToday, true)
  assert.deepEqual(week.map((day) => day.events.map((event) => event.id)), [['span'], ['span'], ['span'], [], [], [], ['later']])
})

test('event detail path keeps the id encoded and hints the month to refetch', () => {
  const t = '2026-10-17'
  assert.equal(eventDetailPath(row({ id: 'notices:1/2', startDate: '2026-11-02', endDate: '2026-11-05' }), t), '/calendar/event/notices%3A1%2F2?d=2026-11-02')
  assert.equal(eventDetailPath(row({ id: 'a', startDate: '2026-10-01', endDate: '2026-10-30' }), t), '/calendar/event/a?d=2026-10-17')
})

// 상태·DOM 구조만 확인한다. CSS 배치는 브라우저 스크린샷으로 따로 본다.
const serverForTest = () => createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })
const json = (value, status = 200) => new Response(JSON.stringify(value), { status })

async function withToday(respond, run) {
  const server = await serverForTest()
  const dom = testDocument()
  dom.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  const originalFetch = globalThis.fetch
  const requests = []
  let root
  let router
  try {
    const { default: TodayPage } = await server.ssrLoadModule('/src/pages/today/TodayPage.tsx')
    globalThis.fetch = async (input) => {
      const url = new URL(input, 'http://localhost')
      requests.push(url)
      return respond(url)
    }
    router = createMemoryRouter([
      { path: '/', element: React.createElement(TodayPage) },
      { path: '/chat', element: React.createElement('p', null, 'CHAT') },
      { path: '/settings', element: React.createElement('p', null, 'SETTINGS') },
      { path: '/calendar/*', element: React.createElement('p', null, 'CALENDAR') },
    ], { initialEntries: ['/'] })
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

const briefing = {
  generatedAt: 'now',
  meals: [{ corner: '경영관 중식 A', menu: '치킨커틀렛' }],
  restaurants: [
    { name: '경영관', corners: [{ corner: '중식 A', menu: '치킨커틀렛' }] },
    { name: '상록원 2층', corners: [{ corner: '중식', menu: '김치찌개' }] },
  ],
  schedules: [],
  notices: [{ title: '장학 신청 안내', url: 'https://www.dongguk.edu/n/1', publishedAt: '2026-10-16', board: '장학공지' }],
}
const calendarEvents = [
  row({ id: 'soon', title: '수강 정정', endDate: addDays(today, 2) }),
  row({ id: 'check', source: 'notices', kind: 'notice', title: '날짜 확인 필요 공지', endDate: addDays(today, 1), dateStatus: 'unverified' }),
]

const respondWith = ({ auth = null, calendarStatus = 200, notifications = [] } = {}) => (url) => {
  if (url.pathname === '/auth/name') return auth ? json(auth) : json({ message: 'unauthorized' }, 401)
  if (url.pathname === '/home/briefing') return json(briefing)
  if (url.pathname === '/calendar/events') return calendarStatus === 200
    ? json({ from: url.searchParams.get('from'), to: url.searchParams.get('to'), generatedAt: 'now', events: calendarEvents })
    : json({ error: 'calendar_unavailable' }, calendarStatus)
  if (url.pathname === '/notifications') return json(notifications)
  return json({}, 404)
}

// 테스트 DOM은 하위 선택자(`h2 span`)·id 선택자를 지원하지 않는다.
const first = (node, tag) => node?.getElementsByTagName(tag)[0]
const section = (dom, title) => Array.from(dom.container.querySelectorAll('section.today-card'))
  .find((node) => first(first(node, 'h2'), 'span')?.textContent === title)

test('guest home shows upcoming confirmed events, meals by restaurant and notices, without a bell', async () => {
  await withToday(respondWith(), async ({ dom, requests }) => {
    const calendarRequest = requests.find((url) => url.pathname === '/calendar/events')
    assert.equal(calendarRequest.searchParams.get('from'), today)
    assert.equal(calendarRequest.searchParams.get('to'), addDays(today, 44))
    assert.equal(requests.some((url) => url.pathname === '/notifications'), false, 'guests do not load notifications')

    const upcoming = section(dom, '다가오는 일정')
    const links = Array.from(upcoming.getElementsByTagName('a')).filter((node) => node.getAttribute('class') === 'today-event')
    assert.deepEqual(links.map((node) => node.querySelector('.today-event__title').textContent), ['수강 정정'])
    assert.equal(links[0].getAttribute('href'), `/calendar/event/soon?d=${addDays(today, 2)}`)
    assert.equal(dom.container.textContent.includes('날짜 확인 필요 공지'), false, 'unverified events stay off the home')

    const meals = section(dom, '오늘 학식')
    const chips = Array.from(meals.getElementsByTagName('button'))
    assert.deepEqual(chips.map((node) => [node.textContent, node.getAttribute('aria-pressed')]), [['경영관', 'true'], ['상록원 2층', 'false']])
    assert.match(meals.textContent, /치킨커틀렛/)
    flushSync(() => props(chips[1]).onClick())
    assert.match(meals.textContent, /김치찌개/)
    assert.doesNotMatch(meals.textContent, /치킨커틀렛/)

    const notice = section(dom, '새 공지').getElementsByTagName('a')[0]
    assert.equal(notice.getAttribute('href'), 'https://www.dongguk.edu/n/1')
    assert.equal(notice.getAttribute('target'), '_blank')
    assert.match(notice.textContent, /장학공지 · 2026-10-16/)
    assert.equal(dom.container.querySelector('.today-bell'), null)
  })
})

test('signed-in home shows the department, an unread badge and a bell into the settings inbox', async () => {
  const notice = (id, isRead) => ({ id, topic: 'scholarship', topicLabel: '장학', source: 'notices', sourceId: id, title: `장학 마감 ${id}`, body: '', targetDate: addDays(today, 3), reminderDate: today, reminderDaysBefore: 3, url: null, isRead, createdTime: new Date().toISOString() })
  const notifications = [notice('n1', false), notice('n2', true)]
  await withToday(respondWith({ auth: { name: '학생', majorName: '경영정보학과' }, notifications }), async ({ dom, router }) => {
    assert.match(first(dom.container.querySelector('.today-header'), 'p').textContent, /경영정보학과/)
    const bell = dom.container.querySelector('.today-bell')
    assert.ok(bell)
    assert.equal(bell.getAttribute('href'), '/settings')
    assert.equal(bell.querySelector('.today-bell__badge').textContent, '1')
    assert.equal(bell.getAttribute('aria-label'), '알림함, 읽지 않은 알림 1개')
    assert.ok(section(dom, '새 공지 · 내 학과 포함'))
    const event = { button: 0, metaKey: false, altKey: false, ctrlKey: false, shiftKey: false, defaultPrevented: false, preventDefault() { this.defaultPrevented = true }, stopPropagation() {}, currentTarget: bell, target: bell }
    flushSync(() => props(bell).onClick(event))
    await settle()
    assert.equal(router.state.location.pathname, '/settings')
    assert.deepEqual(router.state.location.state, { screen: 'inbox' })
  })
})

test('calendar failure shows a retry inside the section; asking opens chat with the question prefilled', async () => {
  let calendarStatus = 503
  await withToday((url) => respondWith({ calendarStatus })(url), async ({ dom, router, requests }) => {
    const upcoming = section(dom, '다가오는 일정')
    const alert = upcoming.querySelector('[role=alert]')
    assert.match(alert.textContent, /잠시 불러올 수 없어요/)
    assert.equal(section(dom, '앞으로 7일'), undefined)
    calendarStatus = 200
    flushSync(() => props(first(alert, 'button')).onClick())
    await settle()
    assert.equal(requests.filter((url) => url.pathname === '/calendar/events').length, 2)
    assert.match(section(dom, '다가오는 일정').textContent, /수강 정정/)

    const input = dom.container.querySelector('input[id=today-ask-input]')
    change(input, '  계절학기 수강신청 언제야?  ')
    const form = dom.container.querySelector('form.today-ask')
    flushSync(() => props(form).onSubmit({ preventDefault() {} }))
    await settle()
    assert.equal(router.state.location.pathname, '/chat')
    assert.deepEqual(router.state.location.state, { prefillQuestion: '계절학기 수강신청 언제야?' })
  })
})
