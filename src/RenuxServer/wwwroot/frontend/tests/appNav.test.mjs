import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const root = new URL('../', import.meta.url).pathname

async function withAppNav(run) {
  const server = await createServer({ configFile: false, root, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })
  try {
    await run({ ...await server.ssrLoadModule('/src/components/appNavItems.ts'), ...await server.ssrLoadModule('/src/components/AppNav.tsx') })
  } finally { await server.close() }
}

const render = (AppNav, path, props = {}) => renderToStaticMarkup(
  React.createElement(MemoryRouter, { initialEntries: [path] }, React.createElement(AppNav, props)),
)
const links = (markup) => [...markup.matchAll(/<a ([^>]*)>[\s\S]*?<\/svg>([^<]*)<\/a>/g)]
  .map(([, attrs, label]) => ({ label, href: attrs.match(/href="([^"]*)"/)?.[1], current: /aria-current="page"/.test(attrs) }))

test('calendar flag is on: default nav exposes 홈, 캘린더, 채팅, 설정; calendar=false hides the calendar link', async () => {
  await withAppNav(({ default: AppNav, CALENDAR_ENABLED, appNavItems }) => {
    assert.equal(CALENDAR_ENABLED, true)
    assert.deepEqual(appNavItems().map(([to]) => to), ['/', '/calendar', '/chat', '/settings'])
    assert.deepEqual(appNavItems(false).map(([to]) => to), ['/', '/chat', '/settings'])
    const markup = render(AppNav, '/', { tabs: true })
    assert.deepEqual(links(markup).map(({ label, href }) => [label, href]), [['홈', '/'], ['캘린더', '/calendar'], ['채팅', '/chat'], ['설정', '/settings']])
    assert.match(markup, /^<nav class="app-nav app-tabs" aria-label="주요 메뉴">/)
    assert.match(render(AppNav, '/'), /^<nav class="app-nav app-side" aria-label="주요 메뉴">/)
    assert.equal((markup.match(/<svg viewBox="0 0 24 24" aria-hidden="true">/g) ?? []).length, 4)
    const hidden = render(AppNav, '/', { tabs: true, calendar: false })
    assert.doesNotMatch(hidden, /calendar|캘린더/)
  })
})

test('calendar flag on: nav exposes 홈, 캘린더, 채팅, 설정 in order', async () => {
  await withAppNav(({ default: AppNav, appNavItems }) => {
    assert.deepEqual(appNavItems(true).map(([to]) => to), ['/', '/calendar', '/chat', '/settings'])
    const items = links(render(AppNav, '/calendar', { tabs: true, calendar: true }))
    assert.deepEqual(items.map(({ label, href }) => [label, href]), [['홈', '/'], ['캘린더', '/calendar'], ['채팅', '/chat'], ['설정', '/settings']])
    assert.deepEqual(items.map(({ current }) => current), [false, true, false, false])
  })
})

test('active state: home only on /, chat on /chat and /chat/:id, exactly one at a time', async () => {
  await withAppNav(({ default: AppNav }) => {
    for (const [path, expected] of [['/', '홈'], ['/chat', '채팅'], ['/chat/abc-123', '채팅'], ['/settings', '설정'], ['/calendar', '캘린더'], ['/calendar/event/x', '캘린더']]) {
      const current = links(render(AppNav, path)).filter((item) => item.current).map((item) => item.label)
      assert.deepEqual(current, [expected], path)
    }
    for (const path of ['/privacy', '/chatter', '/calendars']) {
      assert.deepEqual(links(render(AppNav, path)).filter((item) => item.current), [], path)
    }
  })
})

// 실제 Link 클릭 경로(react-router의 onClick → navigate)를 탄다. CSS·레이아웃은 검증하지 않는다.
test('chat tab: on /chat/:id it starts a new chat (/chat), but stays put while an answer is streaming', async () => {
  const { testDocument, settle } = await import('./domHarness.mjs')
  const { flushSync } = await import('react-dom')
  const { createRoot } = await import('react-dom/client')
  const { createMemoryRouter, RouterProvider } = await import('react-router-dom')
  await withAppNav(async ({ default: AppNav }) => {
    const press = (link) => {
      const event = { button: 0, metaKey: false, altKey: false, ctrlKey: false, shiftKey: false, defaultPrevented: false, preventDefault() { this.defaultPrevented = true }, stopPropagation() {}, currentTarget: link, target: link }
      const key = Object.keys(link).find((name) => name.startsWith('__reactProps$'))
      flushSync(() => link[key].onClick(event))
      return event.defaultPrevented
    }
    const cases = [
      { path: '/chat/abc', busy: false, label: '채팅', expected: '/chat' },
      { path: '/chat/abc', busy: true, label: '채팅', expected: '/chat/abc' },
      { path: '/chat', busy: false, label: '채팅', expected: '/chat' },
      { path: '/', busy: false, label: '채팅', expected: '/chat' },
      { path: '/', busy: false, label: '홈', expected: '/' },
      { path: '/chat/abc', busy: true, label: '홈', expected: '/' },
      { path: '/settings', busy: false, label: '설정', expected: '/settings' },
      { path: '/calendar', busy: false, label: '캘린더', expected: '/calendar' },
      { path: '/chat/abc', busy: false, label: '캘린더', expected: '/calendar' },
    ]
    for (const { path, busy, label, expected } of cases) {
      const dom = testDocument()
      const element = () => React.createElement(AppNav, { tabs: true, busy })
      const router = createMemoryRouter([{ path: '*', element: element() }], { initialEntries: [path] })
      const root = createRoot(dom.container)
      try {
        flushSync(() => root.render(React.createElement(RouterProvider, { router })))
        const link = Array.from(dom.container.getElementsByTagName('a')).find((node) => node.textContent.trim() === label)
        assert.ok(link, `${label} link on ${path}`)
        press(link)
        await settle()
        assert.equal(router.state.location.pathname, expected, `${label} on ${path} (busy=${busy})`)
      } finally {
        flushSync(() => root.unmount())
        router.dispose()
        await new Promise((resolve) => setTimeout(resolve, 20))
        dom.restore()
      }
    }
  })
})
