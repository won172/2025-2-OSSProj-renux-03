import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getCompletionVerification,
  getCompletionRetrieval,
  getGroundingFromEvent,
  parseChatStreamLine,
} from '../src/chat/streamEvents.ts'

const eventLine = (event) => `data: ${JSON.stringify(event)}\r`

test('completion SSE는 네 검증 상태와 관련도 점수 또는 null을 보존한다', () => {
  for (const status of ['passed', 'failed', 'unavailable', 'not_required']) {
    const event = parseChatStreamLine(eventLine({
      type: 'completion',
      verification_status: status,
      relevance_score: status === 'passed' ? 0.8 : null,
      grounded: status === 'passed' ? true : status === 'failed' ? false : null,
    }))
    assert.ok(event)
    assert.equal(getCompletionVerification(event).verificationStatus, status)
    assert.equal(getCompletionVerification(event).relevanceScore, status === 'passed' ? 0.8 : null)
    assert.equal(getGroundingFromEvent(event).grounded, status === 'passed' ? true : status === 'failed' ? false : undefined)
  }
})

test('completion 검색 모드와 제한된 데이터셋 이름을 검증한다', () => {
  for (const [mode, expected] of [
    ['hybrid', 'hybrid'],
    ['sparse_degraded', 'sparse_degraded'],
    ['sparse_only', 'sparse_only'],
    ['unknown', undefined],
    [null, undefined],
  ]) {
    const event = parseChatStreamLine(eventLine({
      type: 'completion', retrieval_mode: mode,
      degraded_datasets: ['courses', 'courses', 'bad name', '../bad', 'notices'],
    }))
    assert.ok(event)
    assert.deepEqual(getCompletionRetrieval(event), {
      retrievalMode: expected,
      degradedDatasets: ['courses', 'notices'],
    })
  }
  assert.deepEqual(getCompletionRetrieval({ type: 'completion' }), {
    retrievalMode: undefined,
    degradedDatasets: undefined,
  })
})

test('이전 completion의 누락 상태와 unknown 상태는 검증 성공으로 해석하지 않는다', () => {
  for (const fields of [{}, { verification_status: 'unknown', relevance_score: '0.9' }]) {
    const event = parseChatStreamLine(eventLine({ type: 'completion', ...fields }))
    assert.ok(event)
    assert.equal(getCompletionVerification(event).verificationStatus, undefined)
    assert.equal(getCompletionVerification(event).relevanceScore, undefined)
    assert.equal(getGroundingFromEvent(event).grounded, undefined)
  }
})

test('grounding SSE에서 grounded가 누락되거나 null이면 확인된 답변으로 바꾸지 않는다', () => {
  for (const fields of [{ score: 0.9 }, { grounded: null, score: 0.9 }]) {
    const event = parseChatStreamLine(eventLine({ type: 'grounding', ...fields }))
    assert.ok(event)
    assert.deepEqual(getGroundingFromEvent(event), { grounded: undefined, groundingScore: 0.9 })
  }
  assert.equal(parseChatStreamLine('event: completion'), null)
})
