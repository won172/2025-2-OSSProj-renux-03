import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { MemoryRouter } from 'react-router-dom'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// Component output tests use synthetic fixtures only. Device layout/focus QA belongs to Orchestrator.
test('citation cards pair each heading and excerpt with its exact number and active card, including same-URL entries', async () => {
  const server = await createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })
  try {
    const { default: SourceCards } = await server.ssrLoadModule('/src/components/chat/SourceCards.tsx')
    const markup = renderToStaticMarkup(React.createElement(SourceCards, {
      sources: [
        { citation_number: 3, chunk_id: 'schedule:exam', source: 'schedule', title: '시험', snippet: '시험 기간', url: 'https://example.edu/calendar' },
        { citation_number: 7, chunk_id: 'schedule:course', source: 'schedule', title: '수강', snippet: '수강 기간', url: 'https://example.edu/calendar' },
      ], showScores: false, activeCitationNumber: 7,
    }))
    const cards = markup.match(/<article[\s\S]*?<\/article>/g)
    assert.equal(cards.length, 2)
    assert.match(cards[0], /data-citation-number="3"/)
    assert.match(cards[0], /시험<\/h4>[\s\S]*시험 기간/)
    assert.doesNotMatch(cards[0], /수강 기간|source-card--active/)
    assert.match(cards[1], /source-card--active[^>]*data-citation-number="7"/)
    assert.match(cards[1], /수강<\/h4>[\s\S]*수강 기간/)
    assert.equal((markup.match(/href="https:\/\/example.edu\/calendar"/g) ?? []).length, 2)
    const missing = renderToStaticMarkup(React.createElement(SourceCards, { sources: [{ citationNumber: 2, title: 'A' }, { title: 'B' }], activeCitationNumber: 9, showScores: false }))
    assert.match(missing, /문서9에 해당하는 출처 번호가 이 응답에 없습니다/)
    assert.match(missing, /번호 없음/)
    assert.equal((missing.match(/data-citation-number="2"/g) ?? []).length, 1)
    const inactive = renderToStaticMarkup(React.createElement(SourceCards, { sources: [{ citationNumber: 2 }, { title: '번호 없음' }], showScores: false }))
    assert.doesNotMatch(inactive, /source-card--active/)
  } finally { await server.close() }
})

test('staff preview shows producer counts, identity, before/after contacts, snapshot metadata and sample limitation', async () => {
  const server = await createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })
  try {
    const { default: Preview } = await server.ssrLoadModule('/src/components/admin/StaffRefreshPreview.tsx')
    const hash = 'b'.repeat(64)
    const data = { schema_version: 1, snapshot_file: `staff-${hash}.json`, snapshot_sha256: hash, created_at: '2026-10-15T10:00:00+09:00', diff: {
      current_rows: 12, incoming_rows: 12, added: 0, removed: 0, contact_changed: 1, changed: true,
      samples: [{ identity: ['fallback', '장학팀', '김**', '직원', '장학'], before_contacts: [['02-111-0000', 'before@example.org']], after_contacts: [['02-111-0001', 'after@example.org']] }],
    } }
    const markup = renderToStaticMarkup(React.createElement(Preview, { data }))
    for (const value of ['12건 / 12건', '연락처 변경', '장학팀', '김**', 'before@example.org', 'after@example.org', '최대 100개', '스냅샷 전체', hash]) assert.ok(markup.includes(value), value)
    const invalid = renderToStaticMarkup(React.createElement(Preview, { data: {} }))
    assert.match(invalid, /role="alert"/)
    assert.match(invalid, /승인할 수 없습니다/)
    assert.doesNotMatch(invalid, /내용이 없습니다/)
  } finally { await server.close() }
})

test('chart exposes full readable values including no-sample gaps, with a labelled percentage axis', async () => {
  const server = await createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })
  try {
    const { default: TrendChart } = await server.ssrLoadModule('/src/components/admin/TrendChart.tsx')
    const markup = renderToStaticMarkup(React.createElement(TrendChart, { points: [{ label: '10/13', value: 1, secondary: 100 }, { label: '10/14', value: 0, secondary: null }, { label: '10/15', value: 2, secondary: 50 }], ariaLabel: '평가 추이' }))
    assert.match(markup, /날짜별 수치 보기/)
    assert.match(markup, /표본 없음/)
    assert.match(markup, /100%/)
    assert.match(markup, /50%/)
    const secondary = markup.match(/class="ac-chart__line ac-chart__line--alt" d="([^"]*)"/)[1]
    assert.equal((secondary.match(/M/g) ?? []).length, 2, 'null data starts a new segment instead of inventing zero satisfaction')
  } finally { await server.close() }
})


test('both authentication forms retain a direct accessible home link', async () => {
  const server = await createServer({ configFile: false, root: new URL('../', import.meta.url).pathname, server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] } })
  try {
    for (const path of ['SignInPage', 'SignUpPage']) {
      const { default: AuthPage } = await server.ssrLoadModule(`/src/pages/auth/${path}.tsx`)
      const markup = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(AuthPage)))
      assert.match(markup, /<a[^>]*class="auth-home-link"[^>]*href="\/"[^>]*>[^<]*홈으로/)
    }
  } finally { await server.close() }
})
