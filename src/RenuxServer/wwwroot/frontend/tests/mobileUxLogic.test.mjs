import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeSources } from '../src/chat/sources.ts'
import { cleanDeadlineSnippet, pageSlice, preferenceKey } from '../src/chat/settingsState.ts'
import { readStaffRefresh, canApproveReview, supportsContentEditing } from '../src/admin/staffReview.ts'
import { toDepartmentKnowledge, toPendingAnswerReview } from '../src/admin/adminData.ts'
import { publicMajorName } from '../src/auth/publicMajor.ts'
import { cronLabel, jobStatus, routeLabel } from '../src/admin/display.ts'
import { importWithRecovery, isModuleLoadError, recoverModuleLoad } from '../src/moduleRecovery.ts'

const staffPayload = () => ({
  schema_version: 1, snapshot_file: `staff-${'a'.repeat(64)}.json`, snapshot_sha256: 'a'.repeat(64),
  created_at: '2026-10-15T10:00:00+09:00',
  diff: { current_rows: 5, incoming_rows: 6, added: 2, removed: 1, contact_changed: 1, changed: true,
    samples: [
      { identity: ['upstream', 'staff-1'], before_contacts: [['02-111-0000', 'old@example.org']], after_contacts: [['02-111-0001', 'new@example.org']] },
      { identity: ['fallback', '학생처 > 장학팀', '김**', '직원', '장학'], before_contacts: [], after_contacts: [['02-111-0002', '']] },
      { identity: ['upstream', 'staff-removed'], before_contacts: [['02-111-0003', '']], after_contacts: [] },
    ],
  },
})

test('same URL citations keep their own number, chunk, title, snippet and scores without cross-record merging', () => {
  const url = 'https://example.edu/calendar'
  const sources = [
    { url, citation_number: 4, chunk_id: 'a', title: '중간고사', snippet: '10월 20일 시험', final_score: 0.4 },
    { url, citationNumber: 9, chunkId: 'b', title: '수강 철회', snippet: '긴 수강 철회 안내와 마감일 10월 30일', finalScore: 0.9 },
    { url, citation_number: 12, chunk_id: 'a', title: '같은 청크의 별도 인용', snippet: '별도 문맥' },
  ]
  const original = structuredClone(sources)
  const result = normalizeSources(sources)
  assert.equal(result.length, 3)
  assert.deepEqual(result.map((s) => [s.citationNumber, s.chunkId, s.title, s.snippet]), [
    [4, 'a', '중간고사', '10월 20일 시험'], [9, 'b', '수강 철회', '긴 수강 철회 안내와 마감일 10월 30일'], [12, 'a', '같은 청크의 별도 인용', '별도 문맥'],
  ])
  assert.equal(result[0].finalScore, 0.4)
  assert.deepEqual(sources, original)
})

test('legacy sources keep original order; partially numbered responses never invent a colliding citation', () => {
  assert.deepEqual(normalizeSources([{ title: 'A' }, { title: 'B' }]).map((s) => s.citationNumber), [1, 2])
  assert.deepEqual(normalizeSources([{ citationNumber: 2 }, { title: '번호가 없는 출처' }, { citation_number: 8 }]).map((s) => s.citationNumber), [2, null, 8])
  assert.deepEqual(normalizeSources([{ citation_number: 0 }, { citation_number: 4 }]).map((s) => s.citationNumber), [null, 4])
})

test('deadline display removes ingestion and OCR headers while retaining all useful content and URL data', () => {
  const item = { url: 'https://example.edu/notice?id=32', snippet: '[게시판: 일반공지, 게시일: 2026-10-01] 신청은 10월 30일까지\n[본문 이미지 전사 · 방식: paddleocr_vl_1.6+apple_vision · SHA-256: abc123]\n제출처: 장학팀, 문의 02-111-0001\n[장학] 지원 자격: 재학생\n[출처] 대학 공식 공지' }
  const before = structuredClone(item)
  assert.equal(cleanDeadlineSnippet(item.snippet), '신청은 10월 30일까지\n\n제출처: 장학팀, 문의 02-111-0001\n[장학] 지원 자격: 재학생\n[출처] 대학 공식 공지')
  assert.deepEqual(item, before)
  assert.equal(cleanDeadlineSnippet('접수 기간 안내 [본문 이미지 전사 · 방식: local-ocr · SHA-256: abc'), '접수 기간 안내')
  assert.equal(cleanDeadlineSnippet('chunk_id: abc\n신청 서류는 재학증명서입니다.\nocr_confidence: 0.9'), '신청 서류는 재학증명서입니다.')
})

test('dirty preference comparison ignores list order, preserves channel, and recognizes per-topic reminder edits', () => {
  const saved = [{ topic: 'scholarship', enabled: true, remindDaysBefore: [7, 1, 0], channel: 'in_app' }, { topic: 'academic_schedule', enabled: false, remindDaysBefore: [1], channel: 'in_app' }]
  const clone = structuredClone(saved).reverse()
  clone[1].remindDaysBefore.reverse()
  assert.equal(preferenceKey(saved), preferenceKey(clone))
  clone[0].enabled = true
  assert.notEqual(preferenceKey(saved), preferenceKey(clone))
  clone[0].enabled = false
  clone[1].remindDaysBefore = [7]
  assert.notEqual(preferenceKey(saved), preferenceKey(clone))
  assert.deepEqual(saved[0].remindDaysBefore, [7, 1, 0])
})

test('bounded settings pages expose every item and clamp after read-notification deletion', () => {
  const items = Array.from({ length: 39 }, (_, id) => ({ id }))
  const visible = Array.from({ length: 5 }, (_, page) => pageSlice(items, page).items).flat()
  assert.deepEqual(visible, items)
  assert.equal(pageSlice(items, 4).items.length, 7)
  assert.equal(pageSlice(items.slice(0, 9), 4).page, 1)
  assert.deepEqual(pageSlice([], 4), { page: 0, pageCount: 1, items: [] })
})

test('staff producer payload maps to readable review, department and dashboard labels; approval uses same guard', () => {
  const raw = staffPayload()
  const item = { id: 27, source_type: 'staff_refresh', data: JSON.stringify(raw), status: 'pending', created_at: raw.created_at }
  const review = toPendingAnswerReview(item)
  assert.equal(readStaffRefresh(raw).error, null)
  assert.equal(review.question, '교직원 전화번호부 갱신')
  assert.match(review.answer, /현재 5건 → 수집 6건.*추가 2.*삭제 1.*연락처 변경 1/)
  assert.equal(review.handler, '교직원 자동 수집')
  assert.equal(toDepartmentKnowledge(item).content, review.answer)
  assert.equal(canApproveReview(review), true)
  assert.equal(supportsContentEditing('staff_refresh'), false)
  assert.equal(canApproveReview({ ...review, status: 'approved' }), false)
})

test('staff approval fails closed for absent, malformed, inconsistent or excessive review data', () => {
  const corruptions = [
    (p) => { delete p.diff }, (p) => { delete p.snapshot_file }, (p) => { p.snapshot_sha256 = 'broken' },
    (p) => { p.schema_version = 2 }, (p) => { p.created_at = 'not a date' },
    (p) => { p.diff.incoming_rows = 0 }, (p) => { p.diff.current_rows = '5' },
    (p) => { p.diff.contact_changed = -1 }, (p) => { p.diff.incoming_rows = 20 },
    (p) => { p.diff.changed = false }, (p) => { p.diff.samples = [] },
    (p) => { p.diff.samples[0].identity = ['upstream'] },
    (p) => { p.diff.samples[0].before_contacts = ['not a contact pair'] },
    (p) => { p.diff.samples[0].after_contacts = [[22, '']] },
    (p) => { p.diff.samples = Array.from({ length: 101 }, () => p.diff.samples[0]) },
  ]
  for (const corrupt of corruptions) {
    const payload = staffPayload(); corrupt(payload)
    assert.equal(readStaffRefresh(payload).payload, null)
    assert.match(readStaffRefresh(payload).error, /승인할 수 없습니다/)
    assert.equal(canApproveReview({ sourceType: 'staff_refresh', raw: payload, status: 'pending' }), false)
  }
  for (const data of [null, [], {}, 'broken']) assert.equal(readStaffRefresh(data).payload, null)
})

test('exactly 100 staff samples remain reviewable; useful samples are not silently truncated', () => {
  const payload = staffPayload()
  payload.diff = { current_rows: 0, incoming_rows: 250, added: 250, removed: 0, contact_changed: 0, changed: true,
    samples: Array.from({ length: 100 }, (_, n) => ({ identity: ['upstream', String(n + 1)], before_contacts: [], after_contacts: [['', `user${n}@example.org`]] })) }
  assert.equal(readStaffRefresh(payload).payload.diff.samples.length, 100)
})

test('unsupported and blank submissions have no approval; supported FAQ/event/notice still do', () => {
  for (const sourceType of ['staff_refresh', 'other', 'custom_knowledge', 'event', 'announcement']) {
    assert.equal(canApproveReview({ sourceType, raw: {}, status: 'pending' }), false)
  }
  for (const [sourceType, raw] of [
    ['custom_knowledge', { question: 'Q', answer: 'A' }],
    ['event', { title: '행사', description: '내용', start_date: '2026-10-15' }],
    ['announcement', { title: '공지', content: '내용', date: '2026-10-15' }],
  ]) {
    assert.equal(canApproveReview({ sourceType, raw, status: 'pending' }), true)
    assert.equal(supportsContentEditing(sourceType), true)
  }
})

test('public personalization excludes only the internal major and retains legitimate student context', () => {
  assert.equal(publicMajorName(' 관리자 '), '')
  assert.equal(publicMajorName('  컴퓨터공학과  '), '컴퓨터공학과')
  assert.equal(publicMajorName('행정학과'), '행정학과')
  assert.equal(publicMajorName(null), '')
})

test('operational labels distinguish partial/pending from failure and never guess complex cron schedules', () => {
  assert.equal(jobStatus('partial').tone, 'warning')
  assert.match(jobStatus('partial').hint, /검수 대기/)
  assert.equal(jobStatus('pending_review').label, '검수 대기')
  assert.equal(jobStatus('failed').tone, 'danger')
  assert.equal(routeLabel('["schedule","notices"]'), '학사일정 · 공지사항')
  assert.equal(routeLabel('staff'), '교직원 연락처')
  assert.equal(cronLabel("cron[month='*', day='*', day_of_week='*', hour='0,6,12,18', minute='0']"), '매일 0:00 · 6:00 · 12:00 · 18:00 (서버 시간대)')
  assert.equal(cronLabel("cron[month='*', day='*', day_of_week='0', hour='4', minute='0']"), '매주 월요일 4:00 (서버 시간대)')
  assert.match(cronLabel("cron[month='2', day='last', hour='4', minute='0']"), /사용자 지정/)
  assert.match(cronLabel("cron[hour='80', minute='0']"), /사용자 지정/)
})

const memoryStorage = () => {
  const values = new Map([['unrelated-user-data', 'preserve me']])
  return { values, getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
}

test('only chunk/module fetch errors qualify for automatic recovery', () => {
  for (const message of ['Failed to fetch dynamically imported module: /a.js', 'error loading dynamically imported module', 'Importing a module script failed.']) {
    assert.equal(isModuleLoadError(new TypeError(message)), true)
  }
  for (const error of [new Error('render failed'), new TypeError('Failed to fetch'), new Error('API returned 500'), null, 'Failed to fetch dynamically imported module']) {
    assert.equal(isModuleLoadError(error), false)
  }
})

test('recovery reloads once per tab session across repeated/different chunk failures without removing data', () => {
  const storage = memoryStorage(); let reloads = 0
  const reload = () => { reloads++ }
  assert.equal(recoverModuleLoad(new TypeError('Failed to fetch dynamically imported module: /a.js'), storage, reload), true)
  for (let n = 0; n < 5; n++) assert.equal(recoverModuleLoad(new TypeError(`Failed to fetch dynamically imported module: /b${n}.js`), storage, reload), false)
  assert.equal(reloads, 1)
  assert.equal(storage.values.get('unrelated-user-data'), 'preserve me')
  assert.equal(storage.values.size, 2)
})

test('offline or blocked session storage retains manual recovery, never an automatic reload loop', () => {
  const failure = new TypeError('Failed to fetch dynamically imported module')
  const storage = memoryStorage(); let reloads = 0
  assert.equal(recoverModuleLoad(failure, storage, () => reloads++, false), false)
  assert.equal(storage.values.size, 1)
  assert.equal(recoverModuleLoad(failure, { getItem: () => null, setItem: () => { throw new Error('quota') } }, () => reloads++), false)
  assert.equal(recoverModuleLoad(failure, { getItem: () => { throw new Error('denied') }, setItem: () => {} }, () => reloads++), false)
  assert.equal(reloads, 0)
})

test('lazy recovery returns successful modules and preserves the original rejection for friendly manual UI', async () => {
  const module = { default: 'page' }
  assert.equal(await importWithRecovery(async () => module), module)
  const error = new Error('render error')
  await assert.rejects(importWithRecovery(async () => { throw error }), (caught) => caught === error)
})

test('ordinary approval required fields match _SUBMIT_REQUIRED_FIELDS exactly, without staff-only restrictions', () => {
  for (const [sourceType, raw] of [
    ['event', { title: '행사', start_date: '2026-10-15' }],
    ['announcement', { title: '공지', content: '공지 본문' }],
    ['custom_knowledge', { question: '질문', answer: '답변' }],
  ]) {
    assert.equal(canApproveReview({ sourceType, raw, status: 'pending' }), true)
    for (const key of Object.keys(raw)) {
      const missing = { ...raw }; delete missing[key]
      assert.equal(canApproveReview({ sourceType, raw: missing, status: 'pending' }), false)
      assert.equal(canApproveReview({ sourceType, raw: { ...raw, [key]: '  ' }, status: 'pending' }), false)
    }
  }
})

test('camelCase history and snake_case stream source identity survive whole-record normalization', () => {
  const history = { citationNumber: 5, sourceRef: 'document:five', chunkId: 'chunk-five', url: 'https://example.edu/shared', title: '다섯 번째', snippet: '다섯 번째 근거', extension: { keep: true } }
  const stream = { citation_number: 8, source_ref: 'document:eight', chunk_id: 'chunk-eight', url: history.url, title: '여덟 번째', snippet: '여덟 번째 근거' }
  const result = normalizeSources([history, stream])
  assert.equal(result[0].sourceRef, history.sourceRef)
  assert.equal(result[0].extension, history.extension)
  assert.equal(result[1].sourceRef, stream.source_ref)
  assert.deepEqual(result.map((source) => [source.citationNumber, source.chunkId, source.title, source.snippet]), [
    [5, 'chunk-five', '다섯 번째', '다섯 번째 근거'], [8, 'chunk-eight', '여덟 번째', '여덟 번째 근거'],
  ])
})
