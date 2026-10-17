import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { createServer } from 'vite'
import { testDocument, props, click, change, settle, button } from './domHarness.mjs'

const serverForTest = () => createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })

test('settings retain unsaved topic/reminder edits across screens and sync; block navigation until saved or discarded', async () => {
  const server = await serverForTest()
  const dom = testDocument()
  const originalFetch = globalThis.fetch
  let root
  let router
  try {
    const { default: Settings } = await server.ssrLoadModule('/src/pages/settings/SettingsPage.tsx')
    let saved = [{ topic: 'scholarship', enabled: true, remindDaysBefore: [7, 1, 0], channel: 'in_app' }]
    const writes = []
    globalThis.fetch = async (input, options = {}) => {
      const path = new URL(input, 'http://localhost').pathname
      if (options.method === 'PUT') { saved = JSON.parse(options.body).preferences; writes.push(saved) }
      const value = path === '/notifications/preferences' ? { preferences: saved }
        : path === '/notifications/sync' ? { created: 0, upcomingMatched: 0 } : []
      return new Response(JSON.stringify(value), { status: 200 })
    }
    router = createMemoryRouter([{ path: '/settings', element: React.createElement(Settings) }, { path: '/', element: React.createElement('p', null, 'HOME') }], { initialEntries: ['/', '/settings'] })
    root = createRoot(dom.container)
    flushSync(() => root.render(React.createElement(RouterProvider, { router })))
    await settle()
    const checkbox = () => dom.container.querySelector('input')
    assert.ok(checkbox(), dom.container.toString())
    assert.equal(props(checkbox()).checked, true)
    const reminder = dom.container.querySelector('.settings-topic__reminders').querySelector('input')
    change(reminder, false)
    change(checkbox(), false)
    assert.equal(props(reminder).disabled, true)
    assert.equal(props(checkbox()).checked, false)
    assert.ok(dom.windowListeners.get('beforeunload').size > 0)
    click(button(dom.container, '알림함'))
    click(button(dom.container, '마감 알림 확인'))
    await settle()
    click(button(dom.container, '관심 주제'))
    assert.equal(props(checkbox()).checked, false, 'sync must not overwrite the unsaved draft')
    assert.ok(button(dom.container, '설정 저장'))
    await router.navigate(-1); await settle()
    assert.equal(router.state.location.pathname, '/settings', 'Back also respects the draft blocker')
    click(button(dom.container, '머무르기'))
    click(button(dom.container, '홈으로'))
    await settle()
    assert.equal(router.state.location.pathname, '/settings')
    assert.ok(button(dom.container, '머무르기'))
    click(button(dom.container, '머무르기'))
    click(button(dom.container, '설정 저장'))
    await settle()
    assert.equal(writes.length, 1)
    assert.equal(writes[0][0].enabled, false)
    assert.deepEqual(writes[0][0].remindDaysBefore, [1, 0], 'disabled topics retain their reminder selection')
    assert.equal(button(dom.container, '저장됨')?.hasAttribute('disabled'), true)
    click(button(dom.container, '홈으로'))
    await settle()
    assert.equal(router.state.location.pathname, '/')
    await router.navigate('/settings'); await settle()
    change(dom.container.querySelector('input'), true)
    click(button(dom.container, '홈으로')); await settle()
    click(button(dom.container, '저장하지 않고 이동')); await settle()
    assert.equal(router.state.location.pathname, '/')
    assert.equal(writes.length, 1, 'discard does not save the draft')
  } finally {
    if (root) flushSync(() => root.unmount())
    router?.dispose()
    await settle()
    globalThis.fetch = originalFetch
    dom.restore()
    await server.close()
  }
})

test('repeated citation selection reopens and focuses the same exact numbered card after collapsing', async () => {
  const server = await serverForTest()
  const dom = testDocument()
  let root
  try {
    const { default: SourceCards } = await server.ssrLoadModule('/src/components/chat/SourceCards.tsx')
    root = createRoot(dom.container)
    const render = (request) => flushSync(() => root.render(React.createElement(SourceCards, { sources: [
      { citation_number: 2, title: '시험', snippet: '시험 발췌', url: 'https://example.edu/calendar' },
      { citation_number: 8, title: '등록', snippet: '등록 발췌', url: 'https://example.edu/calendar' },
    ], activeCitationNumber: 8, citationRequest: request, showScores: false })))
    render(1); await new Promise((resolve) => setTimeout(resolve, 10)); await settle()
    const card = dom.container.querySelector('[data-citation-number="8"]')
    assert.equal(dom.document.activeElement, card)
    assert.equal(card.scrollCount, 1)
    const details = dom.container.querySelector('details')
    flushSync(() => props(details).onToggle({ currentTarget: { open: false } }))
    assert.equal(props(details).open, false)
    render(2); await new Promise((resolve) => setTimeout(resolve, 10)); await settle()
    assert.equal(props(details).open, true)
    assert.equal(card.scrollCount, 2)
    assert.equal(dom.document.activeElement, card)
  } finally {
    if (root) flushSync(() => root.unmount())
    await settle()
    dom.restore(); await server.close()
  }
})

const adminContext = { role: 'UNIVERSITY_COUNCIL', userName: '담당자', majorName: '컴퓨터공학과', isUniversityLevel: true, dataVersion: 0, showToast: () => '', notifyDataChanged: () => {}, dismissToast: () => {} }

test('department master/detail reveals selection and new forms above a 35-item list; all three submission contracts and validation survive', async () => {
  const server = await serverForTest(); const dom = testDocument(); const originalFetch = globalThis.fetch
  let root
  try {
    const { default: Department } = await server.ssrLoadModule('/src/pages/admin/DepartmentAdminPage.tsx')
    const { AdminConsoleContext } = await server.ssrLoadModule('/src/components/admin/adminConsoleContext.ts')
    const requests = []
    const items = Array.from({ length: 35 }, (_, id) => ({ id: id + 1, source_type: 'custom_knowledge', status: 'pending', created_at: '2026-10-15T10:00:00+09:00', data: JSON.stringify({ question: `질문 ${id + 1}`, answer: `답변 ${id + 1}` }) }))
    globalThis.fetch = async (input, options = {}) => {
      if (options.method === 'POST') { requests.push({ input, body: JSON.parse(options.body) }); return new Response(JSON.stringify({ status: 'pending', id: 36 })) }
      return new Response(JSON.stringify(items))
    }
    root = createRoot(dom.container)
    flushSync(() => root.render(React.createElement(AdminConsoleContext.Provider, { value: adminContext }, React.createElement(Department))))
    await settle()
    const firstRow = dom.container.querySelector('.ac-list-item')
    firstRow.focus(); click(firstRow)
    assert.ok(dom.container.querySelector('.ac-master-detail--open'))
    assert.equal(dom.document.activeElement.tagName, 'h2')
    click(button(dom.container, '← 목록으로')); await settle()
    assert.equal(dom.container.querySelector('.ac-master-detail--open'), null)
    for (const type of ['knowledge', 'event', 'announcement']) {
      click(button(dom.container, '+ 새 정보 등록'))
      assert.ok(dom.container.querySelector('.ac-master-detail--open'))
      assert.equal(dom.document.activeElement.getAttribute('id'), 'dept-content-type')
      const field = (id) => dom.container.querySelector(`[id="${id}"]`)
      change(field('dept-content-type'), type)
      change(field('dept-title'), `${type} 제목`)
      change(field('dept-content'), `${type} 내용`)
      change(field('dept-visibility'), 'public')
      if (type !== 'knowledge') change(field('dept-start-date'), '2026-10-15')
      if (type === 'event') {
        change(field('dept-end-date'), '2026-10-14')
        const count = requests.length
        flushSync(() => props(dom.container.querySelector('form')).onSubmit({ preventDefault() {} }))
        assert.equal(requests.length, count)
        assert.match(dom.container.textContent, /종료일은 시작일보다 빠를 수 없습니다/)
        change(field('dept-end-date'), '2026-10-16'); change(field('dept-location'), '공학관')
      }
      if (type === 'announcement') change(field('dept-category'), '학사')
      click(button(dom.container, '챗봇 미리보기'))
      assert.match(dom.container.querySelector('.ac-preview').textContent, new RegExp(`${type} 내용`))
      flushSync(() => props(dom.container.querySelector('form')).onSubmit({ preventDefault() {} }))
      await settle()
      assert.equal(dom.container.querySelector('.ac-master-detail--open'), null)
    }
    assert.deepEqual(requests.map((r) => r.body.source_type), ['custom_knowledge', 'event', 'announcement'])
    const payloads = requests.map((r) => JSON.parse(r.body.data))
    for (const payload of payloads) { assert.equal(payload.visibility, 'public'); assert.equal(payload.department, '컴퓨터공학과'); assert.equal(payload.requester, '담당자') }
    assert.equal(payloads[1].location, '공학관'); assert.equal(payloads[1].end_date, '2026-10-16')
    assert.equal(payloads[2].category, '학사')
  } finally { if (root) flushSync(() => root.unmount()); await settle(); globalThis.fetch = originalFetch; dom.restore(); await server.close() }
})

test('staff review blocks malformed single and bulk approval, hides unsupported editing, and preserves approval/rejection endpoints', async () => {
  const server = await serverForTest(); const dom = testDocument(); const originalFetch = globalThis.fetch
  let root; let router
  try {
    const { default: Review } = await server.ssrLoadModule('/src/pages/admin/ReviewPage.tsx')
    const { AdminConsoleContext } = await server.ssrLoadModule('/src/components/admin/adminConsoleContext.ts')
    const digest = 'a'.repeat(64)
    const valid = { schema_version: 1, snapshot_file: `staff-${digest}.json`, snapshot_sha256: digest, created_at: '2026-10-15T10:00:00+09:00', diff: { current_rows: 1, incoming_rows: 2, added: 1, removed: 0, contact_changed: 0, changed: true, samples: [{ identity: ['upstream', 'staff-2'], before_contacts: [], after_contacts: [['02-111-2222', '']] }] } }
    const items = [
      { id: 1, source_type: 'staff_refresh', status: 'pending', data: '{}', created_at: '2026-10-15T11:00:00+09:00' },
      { id: 2, source_type: 'staff_refresh', status: 'pending', data: JSON.stringify(valid), created_at: '2026-10-15T10:00:00+09:00' },
    ]
    const posts = []
    globalThis.fetch = async (input, options = {}) => {
      if (options.method === 'POST') { posts.push({ input, body: JSON.parse(options.body) }); return new Response('{}') }
      return new Response(JSON.stringify(items))
    }
    router = createMemoryRouter([{ path: '/', element: React.createElement(AdminConsoleContext.Provider, { value: adminContext }, React.createElement(Review)) }])
    root = createRoot(dom.container); flushSync(() => root.render(React.createElement(RouterProvider, { router }))); await settle()
    const approve = button(dom.container.querySelector('.ac-detail'), '승인')
    assert.equal(approve.hasAttribute('disabled'), true)
    click(approve); await settle()
    assert.equal(posts.length, 0, 'handler must also reject malformed payload even if invoked directly')
    assert.equal(button(dom.container, '수정'), undefined)
    assert.equal(button(dom.container, '챗봇 미리보기'), undefined)
    const checkboxes = Array.from(dom.container.querySelector('.ac-list').getElementsByTagName('input'))
    change(checkboxes[0], true); change(checkboxes[1], true)
    assert.equal(button(dom.container, '일괄 승인').hasAttribute('disabled'), true)
    click(button(dom.container, '선택 해제'))
    click(button(dom.container, '반려…'))
    assert.equal(button(dom.container.querySelector('.ac-modal'), '반려').hasAttribute('disabled'), true)
    change(dom.container.querySelector('textarea'), '검토 자료 누락')
    click(button(dom.container.querySelector('.ac-modal'), '반려')); await settle()
    assert.equal(posts[0].input, '/admin/reject/1')
    assert.deepEqual(posts[0].body, { note: '검토 자료 누락' })
    const row = Array.from(dom.container.querySelector('.ac-list').getElementsByTagName('button')).at(-1)
    click(row)
    assert.equal(button(dom.container.querySelector('.ac-detail'), '승인').hasAttribute('disabled'), false)
    click(button(dom.container.querySelector('.ac-detail'), '승인')); await settle()
    assert.equal(posts[1].input, '/admin/approve/2')
    assert.deepEqual(posts[1].body, { note: null })
  } finally { if (root) flushSync(() => root.unmount()); router?.dispose(); await settle(); globalThis.fetch = originalFetch; dom.restore(); await server.close() }
})

test('modal rerenders preserve typing focus; busy Escape cannot bypass confirmation protection', async () => {
  const server = await serverForTest(); const dom = testDocument(); let root
  try {
    const { default: Modal } = await server.ssrLoadModule('/src/components/admin/Modal.tsx')
    let cancelled = 0
    root = createRoot(dom.container)
    const render = (busy) => flushSync(() => root.render(React.createElement(Modal, { open: true, title: '확인', busy, onCancel: () => cancelled++, onConfirm: () => {} }, React.createElement('input', { defaultValue: 'draft' }))))
    render(false)
    const input = dom.container.querySelector('input'); const focusCount = input.focusCount
    render(false)
    assert.equal(input.focusCount, focusCount, 'inline callbacks changing must not restart focus lifecycle')
    render(true)
    for (const handler of dom.listeners.get('keydown')) handler({ key: 'Escape', preventDefault() {}, stopPropagation() {} })
    assert.equal(cancelled, 0)
    render(false)
    for (const handler of dom.listeners.get('keydown')) handler({ key: 'Escape', preventDefault() {}, stopPropagation() {} })
    assert.equal(cancelled, 1)
  } finally { if (root) flushSync(() => root.unmount()); await settle(); dom.restore(); await server.close() }
})

test('settings paginate deadlines and reminder events, retain read/clear endpoints, and keep deletion locked until both confirmations', async () => {
  const server = await serverForTest(); const dom = testDocument(); const originalFetch = globalThis.fetch
  let root; let router
  try {
    const { default: Settings } = await server.ssrLoadModule('/src/pages/settings/SettingsPage.tsx')
    const targetDate = '2099-10-15T00:00:00+09:00'
    const deadlines = Array.from({ length: 39 }, (_, n) => ({ id: `deadline-${n}`, title: `마감 ${n}`, source: 'notices', sourceId: String(n), sourceLabel: '공지', topic: 'scholarship', topicLabel: '장학', targetDate, dDay: 1, snippet: '준비물과 신청 방법', url: `https://example.edu/${n}` }))
    let notifications = Array.from({ length: 14 }, (_, n) => ({ id: `notification-${n}`, title: `알림 ${n}`, body: '알림 상세 내용', source: 'notices', sourceId: String(Math.floor(n / 2)), topic: 'scholarship', topicLabel: '장학', targetDate, reminderDaysBefore: n % 2 ? 1 : 7, createdTime: '2026-10-15T10:00:00+09:00', isRead: false }))
    const calls = []
    globalThis.fetch = async (input, options = {}) => {
      calls.push({ input, method: options.method ?? 'GET' })
      if (input === '/auth/account') return new Response(JSON.stringify({ message: '비밀번호를 확인해주세요.' }), { status: 400 })
      if (input === '/notifications/read-all') { notifications = notifications.map((n) => ({ ...n, isRead: true })); return new Response('{"updated":14}') }
      if (input === '/notifications/read') { notifications = []; return new Response('{"deleted":14}') }
      const value = input === '/notifications/preferences' ? { preferences: [{ topic: 'scholarship', enabled: true, remindDaysBefore: [7, 1, 0], channel: 'in_app' }] }
        : input === '/notifications/deadlines' ? deadlines : notifications
      return new Response(JSON.stringify(value))
    }
    router = createMemoryRouter([{ path: '/', element: React.createElement(Settings) }])
    root = createRoot(dom.container); flushSync(() => root.render(React.createElement(RouterProvider, { router }))); await settle()
    click(button(dom.container, '내 마감'))
    const deadlinePanel = dom.container.querySelector('[id="settings-deadlines"]')
    assert.equal(deadlinePanel.querySelectorAll('article').length, 8)
    assert.equal(dom.container.querySelector('[id="settings-inbox"]').hasAttribute('hidden'), true)
    for (let n = 0; n < 4; n++) click(button(deadlinePanel, '다음'))
    assert.equal(deadlinePanel.querySelectorAll('article').length, 7)
    assert.match(deadlinePanel.textContent, /마감 38/)
    click(button(dom.container, '알림함'))
    const inbox = dom.container.querySelector('[id="settings-inbox"]')
    assert.equal(inbox.querySelectorAll('article').length, 8)
    assert.match(inbox.textContent, /다가오는 마감 7건 · 읽지 않은 리마인드 14회/)
    click(button(inbox, '다음')); assert.equal(inbox.querySelectorAll('article').length, 6)
    click(button(inbox, '모두 읽음')); await settle()
    assert.match(inbox.textContent, /읽지 않은 리마인드 0회/)
    click(button(inbox, '읽은 알림 정리')); await settle()
    assert.equal(inbox.querySelectorAll('article').length, 0)
    assert.equal(button(inbox, '다음'), undefined)
    click(button(dom.container, '계정')); click(button(dom.container, '회원 탈퇴'))
    const dialog = dom.container.querySelector('.ch-dialog')
    assert.equal(button(dialog, '영구 삭제').hasAttribute('disabled'), true)
    click(button(dialog, '영구 삭제')); await settle()
    assert.equal(calls.some((call) => call.input === '/auth/account'), false)
    const inputs = Array.from(dialog.getElementsByTagName('input'))
    change(inputs[0], 'synthetic-test-value'); change(inputs[1], '잘못된 문구')
    assert.equal(button(dialog, '영구 삭제').hasAttribute('disabled'), true)
    change(inputs[1], '회원 탈퇴'); click(button(dialog, '영구 삭제')); await settle()
    assert.equal(calls.filter((call) => call.input === '/auth/account').length, 1)
    assert.match(dialog.textContent, /비밀번호를 확인해주세요/)
    assert.ok(calls.some((call) => call.input === '/notifications/read-all' && call.method === 'POST'))
    assert.ok(calls.some((call) => call.input === '/notifications/read' && call.method === 'DELETE'))
  } finally { if (root) flushSync(() => root.unmount()); router?.dispose(); await settle(); globalThis.fetch = originalFetch; dom.restore(); await server.close() }
})

test('approved review deep link wins over a pending first item and is consumed only after history resolves', async () => {
  const server = await serverForTest(); const dom = testDocument(); const originalFetch = globalThis.fetch
  let root; let router
  try {
    const { default: Review } = await server.ssrLoadModule('/src/pages/admin/ReviewPage.tsx')
    const { AdminConsoleContext } = await server.ssrLoadModule('/src/components/admin/adminConsoleContext.ts')
    const pending = { id: 1, source_type: 'custom_knowledge', status: 'pending', created_at: '2026-10-15T12:00:00+09:00', data: JSON.stringify({ question: 'PENDING QUESTION', answer: '답변' }) }
    const approved = { id: 2, source_type: 'announcement', status: 'approved', created_at: '2026-10-14T12:00:00+09:00', data: JSON.stringify({ title: 'APPROVED TARGET', content: '승인 내용' }) }
    let resolveHistory
    globalThis.fetch = async (input) => new URL(input, 'http://localhost').pathname === '/admin/items'
      ? new Promise((resolve) => { resolveHistory = () => resolve(new Response(JSON.stringify([pending, approved]))) })
      : new Response(JSON.stringify([pending]))
    router = createMemoryRouter([{ path: '/admin/review', element: React.createElement(AdminConsoleContext.Provider, { value: adminContext }, React.createElement(Review)) }], { initialEntries: ['/admin/review?item=2'] })
    root = createRoot(dom.container); flushSync(() => root.render(React.createElement(RouterProvider, { router }))); await settle()
    assert.equal(router.state.location.search, '?item=2')
    resolveHistory(); await settle()
    assert.match(dom.container.querySelector('.ac-detail__title').textContent, /APPROVED TARGET/)
    assert.ok(dom.container.querySelector('.ac-master-detail--open'))
    assert.equal(router.state.location.search, '')
    assert.equal(button(dom.container, '전체').getAttribute('aria-pressed'), 'true')
    assert.doesNotMatch(dom.container.querySelector('.ac-detail').textContent, /PENDING QUESTION/)
  } finally { if (root) flushSync(() => root.unmount()); router?.dispose(); await settle(); globalThis.fetch = originalFetch; dom.restore(); await server.close() }
})

test('independent settings fetch failures keep account/navigation and scope errors; 401 still guards all account screens', async () => {
  const server = await serverForTest(); const dom = testDocument(); const originalFetch = globalThis.fetch
  let root; let router
  try {
    const { default: Settings } = await server.ssrLoadModule('/src/pages/settings/SettingsPage.tsx')
    for (const failure of ['inbox', 'preferences', 'deadlines', 'unauthorized']) {
      globalThis.fetch = async (input) => {
        const path = new URL(input, 'http://localhost').pathname
        const failedPath = failure === 'inbox' || failure === 'unauthorized' ? '/notifications' : `/notifications/${failure}`
        if (path === failedPath) return new Response('{}', { status: failure === 'unauthorized' ? 401 : 500 })
        return new Response(JSON.stringify(path === '/notifications/preferences' ? { preferences: [{ topic: 'scholarship', enabled: true, remindDaysBefore: [7], channel: 'in_app' }] } : []))
      }
      router = createMemoryRouter([{ path: '/', element: React.createElement(Settings) }])
      root = createRoot(dom.container); flushSync(() => root.render(React.createElement(RouterProvider, { router }))); await settle()
      if (failure === 'unauthorized') {
        assert.ok(button(dom.container, '로그인하기'))
        assert.equal(button(dom.container, '계정'), undefined)
      } else {
        assert.ok(button(dom.container, '계정'))
        click(button(dom.container, '계정'))
        assert.equal(dom.container.querySelector('[id="settings-account"]').hasAttribute('hidden'), false)
        assert.ok(button(dom.container, '회원 탈퇴'))
        click(button(dom.container, '관심 주제'))
        if (failure === 'preferences') {
          assert.equal(button(dom.container, '설정 저장').hasAttribute('disabled'), true)
          assert.ok(button(dom.container, '관심 주제 다시 시도'))
        } else {
          change(dom.container.querySelector('input'), false)
          assert.equal(button(dom.container, '설정 저장').hasAttribute('disabled'), false)
        }
        click(button(dom.container, '알림함'))
        if (failure === 'inbox') assert.ok(button(dom.container, '알림함 다시 시도'))
        else assert.match(dom.container.querySelector('[id="settings-inbox"]').textContent, /아직 생성된 알림이 없습니다/)
      }
      flushSync(() => root.unmount()); root = null; router.dispose(); router = null; await settle()
    }
  } finally { if (root) flushSync(() => root.unmount()); router?.dispose(); await settle(); globalThis.fetch = originalFetch; dom.restore(); await server.close() }
})

test('initial and post-sync inbox request the same explicit past-inclusive server maximum and label only the loaded range', async () => {
  const server = await serverForTest(); const dom = testDocument(); const originalFetch = globalThis.fetch
  let root; let router
  try {
    const { default: Settings } = await server.ssrLoadModule('/src/pages/settings/SettingsPage.tsx')
    const queries = []
    const past = { id: 'past-1', title: '지난 마감의 미확인 알림', body: '과거 알림 본문', source: 'notices', sourceId: 'past', topic: 'scholarship', topicLabel: '장학', targetDate: '2000-10-01T00:00:00+09:00', reminderDaysBefore: 1, createdTime: '2000-09-30T00:00:00+09:00', isRead: false }
    globalThis.fetch = async (input) => {
      const url = new URL(input, 'http://localhost')
      if (url.pathname === '/notifications') { queries.push(url.search); return new Response(JSON.stringify(url.searchParams.get('includePast') === 'true' ? [past] : [])) }
      return new Response(JSON.stringify(url.pathname === '/notifications/preferences' ? { preferences: [] } : url.pathname === '/notifications/sync' ? { created: 0, upcomingMatched: 0 } : []))
    }
    router = createMemoryRouter([{ path: '/', element: React.createElement(Settings) }]); root = createRoot(dom.container)
    flushSync(() => root.render(React.createElement(RouterProvider, { router }))); await settle()
    click(button(dom.container, '알림함')); const inbox = dom.container.querySelector('[id="settings-inbox"]')
    assert.match(inbox.textContent, /지난 마감의 미확인 알림/)
    assert.match(inbox.textContent, /최대 100회만 조회/)
    assert.match(inbox.textContent, /전체 이력이 아닙니다/)
    assert.match(inbox.textContent, /불러온 1회 중 읽지 않은 다가오는 마감 0건 · 읽지 않은 리마인드 1회/)
    click(button(inbox, '마감 알림 확인')); await settle()
    assert.deepEqual(queries, ['?includePast=true&limit=100', '?includePast=true&limit=100'])
  } finally { if (root) flushSync(() => root.unmount()); router?.dispose(); await settle(); globalThis.fetch = originalFetch; dom.restore(); await server.close() }
})

test('master/detail keeps the original list opener through resubmission and uses the visible list when it is gone or hidden', async () => {
  const server = await serverForTest(); const dom = testDocument(); let root
  try {
    const { useMasterDetail } = await server.ssrLoadModule('/src/hooks/useMasterDetail.ts')
    let detail
    function Harness({ removed = false, hidden = false }) {
      detail = useMasterDetail()
      return React.createElement(React.Fragment, null,
        React.createElement('div', { ref: detail.listRef, tabIndex: -1, hidden: detail.shown, 'aria-label': '목록' }, !removed && React.createElement('button', { hidden }, '목록 항목')),
        React.createElement('div', { ref: detail.detailRef, hidden: !detail.shown, tabIndex: -1 }, React.createElement('h2', { tabIndex: -1 }, '상세'), React.createElement('button', null, '재제출'), React.createElement('input')))
    }
    root = createRoot(dom.container)
    const render = (extra = {}) => flushSync(() => root.render(React.createElement(Harness, extra)))
    render()
    for (const mode of ['retained', 'removed', 'hidden']) {
      render()
      const opener = button(dom.container, '목록 항목'); opener.focus()
      flushSync(() => detail.reveal())
      button(dom.container, '재제출').focus(); flushSync(() => detail.reveal(true))
      assert.equal(dom.document.activeElement.tagName, 'input')
      if (mode !== 'retained') render({ [mode]: true })
      flushSync(() => detail.back()); await new Promise((resolve) => setTimeout(resolve, 10)); await settle()
      assert.equal(dom.document.activeElement, mode === 'retained' ? opener : detail.listRef.current)
      assert.equal(dom.document.activeElement.closest('[hidden]'), null)
    }
  } finally { if (root) flushSync(() => root.unmount()); await settle(); dom.restore(); await server.close() }
})

test('settings open the screen another page asks for (home bell → inbox) and ignore unknown screens', async () => {
  for (const [state, expected] of [[{ screen: 'inbox' }, 'inbox'], [{ screen: 'nope' }, 'preferences'], [null, 'preferences']]) {
    const server = await serverForTest(); const dom = testDocument(); const originalFetch = globalThis.fetch
    let root; let router
    try {
      const { default: Settings } = await server.ssrLoadModule('/src/pages/settings/SettingsPage.tsx')
      globalThis.fetch = async (input) => {
        const url = new URL(input, 'http://localhost')
        return new Response(JSON.stringify(url.pathname === '/notifications/preferences' ? { preferences: [] } : []))
      }
      router = createMemoryRouter([{ path: '/settings', element: React.createElement(Settings) }], { initialEntries: [{ pathname: '/settings', state }] })
      root = createRoot(dom.container)
      flushSync(() => root.render(React.createElement(RouterProvider, { router }))); await settle()
      for (const id of ['preferences', 'deadlines', 'inbox', 'account']) {
        assert.equal(dom.container.querySelector(`[id="settings-${id}"]`).hasAttribute('hidden'), id !== expected, `${JSON.stringify(state)} → ${id}`)
      }
    } finally { if (root) flushSync(() => root.unmount()); router?.dispose(); await settle(); globalThis.fetch = originalFetch; dom.restore(); await server.close() }
  }
})
