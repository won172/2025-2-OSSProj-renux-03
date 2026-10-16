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

test('calendar flag is off by default: nav exposes only 채팅 and 설정 and no calendar link', async () => {
  await withAppNav(({ default: AppNav, CALENDAR_ENABLED, appNavItems }) => {
    assert.equal(CALENDAR_ENABLED, false)
    assert.deepEqual(appNavItems().map(([to]) => to), ['/', '/settings'])
    const markup = render(AppNav, '/', { tabs: true })
    assert.deepEqual(links(markup).map(({ label, href }) => [label, href]), [['채팅', '/'], ['설정', '/settings']])
    assert.doesNotMatch(markup, /calendar|캘린더/)
    assert.match(markup, /^<nav class="app-nav app-tabs" aria-label="주요 메뉴">/)
    assert.match(render(AppNav, '/'), /^<nav class="app-nav app-side" aria-label="주요 메뉴">/)
    assert.equal((markup.match(/<svg viewBox="0 0 24 24" aria-hidden="true">/g) ?? []).length, 2)
  })
})

test('calendar flag on: nav exposes 채팅, 캘린더, 설정 in order', async () => {
  await withAppNav(({ default: AppNav, appNavItems }) => {
    assert.deepEqual(appNavItems(true).map(([to]) => to), ['/', '/calendar', '/settings'])
    const items = links(render(AppNav, '/calendar', { tabs: true, calendar: true }))
    assert.deepEqual(items.map(({ label, href }) => [label, href]), [['채팅', '/'], ['캘린더', '/calendar'], ['설정', '/settings']])
    assert.deepEqual(items.map(({ current }) => current), [false, true, false])
  })
})

test('active state: chat tab is current on home and chat routes, settings on /settings, exactly one at a time', async () => {
  await withAppNav(({ default: AppNav }) => {
    for (const [path, expected] of [['/', '채팅'], ['/chat/abc-123', '채팅'], ['/settings', '설정']]) {
      const current = links(render(AppNav, path)).filter((item) => item.current).map((item) => item.label)
      assert.deepEqual(current, [expected], path)
    }
    assert.deepEqual(links(render(AppNav, '/privacy')).filter((item) => item.current), [])
  })
})
