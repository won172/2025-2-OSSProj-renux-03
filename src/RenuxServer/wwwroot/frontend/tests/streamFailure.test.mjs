import assert from 'node:assert/strict'
import test from 'node:test'
import { getChatStreamFailureMessage, getFailedAssistantContent } from '../src/chat/streamFailure.ts'

test('an authenticated 401 explains that login is required rather than claiming a disconnected answer', () => {
  const message = getChatStreamFailureMessage({ status: 401 }, true)
  assert.match(message, /다시 로그인/)
  assert.doesNotMatch(message, /응답 연결이 끊겼/)
  assert.equal(getFailedAssistantContent('', message), message)
})

test('an invalid guest identity asks for a new conversation and ownership failures remain distinct', () => {
  assert.match(getChatStreamFailureMessage({ status: 401 }, false), /새 대화/)
  assert.match(getChatStreamFailureMessage({ status: 403 }, true), /접근할 수 없습니다/)
})

test('a disconnected SSE retains delivered text and warns that the response is incomplete', () => {
  const message = getChatStreamFailureMessage(new Error('transport failed'), true)
  assert.match(message, /응답 연결이 끊겼/)
  assert.equal(getFailedAssistantContent('이미 도착한 답변', message), `이미 도착한 답변\n\n${message}`)
  assert.equal(getFailedAssistantContent('  ', message), message)
})
