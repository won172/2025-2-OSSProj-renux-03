import assert from 'node:assert/strict'
import test from 'node:test'

import { Capacitor } from '@capacitor/core'

import { loadChatMessages, mapChatHistoryMessage } from '../src/chat/chatApi.ts'
import { getVerificationNoteKind } from '../src/chat/chatState.ts'

const answer = {
  id: 'answer-1',
  chatId: 'chat-1',
  isAsk: false,
  content: '답변',
  createdTime: '2026-09-28T00:00:00Z',
  grounded: false,
}

test('히스토리의 네 검증 상태는 실시간 답변과 같은 안내로 표시한다', () => {
  for (const [status, expectedNote] of [
    ['passed', null],
    ['failed', 'failed'],
    ['unavailable', 'unavailable'],
    ['not_required', null],
  ]) {
    const mapped = mapChatHistoryMessage({ ...answer, verificationStatus: status, relevanceScore: 0.8 })
    assert.equal(mapped.verificationStatus, status)
    assert.equal(mapped.relevanceScore, 0.8)
    assert.equal(getVerificationNoteKind(mapped), expectedNote)
  }
})

test('상태가 없거나 알 수 없으면 이전 grounded 표시를 유지한다', () => {
  for (const fields of [{}, { verificationStatus: null }, { verificationStatus: 'unknown' }]) {
    const mapped = mapChatHistoryMessage({ ...answer, ...fields, relevanceScore: null })
    assert.equal(mapped.verificationStatus, undefined)
    assert.equal(mapped.relevanceScore, null)
    assert.equal(getVerificationNoteKind(mapped), 'failed')
  }
})

test('/chat/load 응답의 검증 상태가 화면 메시지에 전달된다', async (t) => {
  t.mock.method(Capacitor, 'isNativePlatform', () => false)
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => ({
    ok: true,
    text: async () => JSON.stringify([{ ...answer, verificationStatus: 'unavailable', relevanceScore: 0.4 }]),
  }))

  const messages = await loadChatMessages('chat-1', '2026-09-28T01:00:00Z')
  assert.equal(fetchMock.mock.calls[0].arguments[0], '/chat/load')
  assert.equal(messages[0].verificationStatus, 'unavailable')
  assert.equal(messages[0].relevanceScore, 0.4)
  assert.equal(getVerificationNoteKind(messages[0]), 'unavailable')
})

test('/chat/load 검색 저하 정보가 화면 메시지에 전달되고 이전 기록은 비어 있다', async (t) => {
  t.mock.method(Capacitor, 'isNativePlatform', () => false)
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: true,
    text: async () => JSON.stringify([
      { ...answer, retrievalMode: 'sparse_degraded', degradedDatasets: ['courses', 'courses'] },
      { ...answer, id: 'answer-2', retrievalMode: 'unknown', degradedDatasets: ['bad name'] },
      { ...answer, id: 'answer-3' },
    ]),
  }))

  const messages = await loadChatMessages('chat-1', '2026-09-28T01:00:00Z')
  assert.equal(messages[0].retrievalMode, 'sparse_degraded')
  assert.deepEqual(messages[0].degradedDatasets, ['courses'])
  assert.equal(messages[1].retrievalMode, undefined)
  assert.deepEqual(messages[1].degradedDatasets, [])
  assert.equal(messages[2].retrievalMode, undefined)
  assert.equal(messages[2].degradedDatasets, undefined)
})
