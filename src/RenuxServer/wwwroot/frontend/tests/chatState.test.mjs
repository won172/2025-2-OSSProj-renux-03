import assert from 'node:assert/strict'
import test from 'node:test'

import {
  finalizeStoppedAssistant,
  getVerificationNoteKind,
  normalizeAssistantRuns,
  parseGuestChatRecords,
  prepareRegeneration,
  readGuestChatRecords,
  resolveGuestChatRoute,
  toChatPath,
  updateGuestChatMessages,
  upsertGuestChat,
  writeGuestChatRecords,
} from '../src/chat/chatState.ts'
import { GUEST_TOKEN_HEADER, withGuestTokenHeader } from '../src/chat/guestToken.ts'

const question = {
  id: 'question-1',
  chatId: 'chat-1',
  isAsk: true,
  content: '장학 공지 알려줘',
  createdTime: '2026-07-19T00:00:00Z',
}

const firstAnswer = {
  id: 'answer-1',
  chatId: 'chat-1',
  isAsk: false,
  content: '이전 답변',
  createdTime: '2026-07-19T00:00:01Z',
}

const latestAnswer = {
  ...firstAnswer,
  id: 'answer-2',
  content: '최신 답변\n\n## 확인된 정보 1',
  createdTime: '2026-07-19T00:00:02Z',
}

test('연속 assistant 응답은 최신값만 남기고 Markdown을 보존한다', () => {
  assert.deepEqual(normalizeAssistantRuns([question, firstAnswer, latestAnswer]), [question, latestAnswer])
})

test('로드된 빈 assistant 행은 영구 타이핑 표시 대신 완료되지 않은 상태로 정규화한다', () => {
  const emptyAnswer = { ...latestAnswer, content: '' }
  const normalized = normalizeAssistantRuns([question, firstAnswer, emptyAnswer])

  assert.equal(normalized.length, 2)
  assert.equal(normalized[1].id, emptyAnswer.id)
  assert.equal(normalized[1].content, '답변 생성이 완료되지 않았습니다.')
})

test('기존 배열형 게스트 저장 포맷을 읽고 known/unknown URL을 구분한다', () => {
  const records = parseGuestChatRecords(JSON.stringify([{ id: 'chat-1', title: '장학 상담' }]))

  assert.equal('guestToken' in records[0], false)
  assert.equal(resolveGuestChatRoute(undefined, records).kind, 'root')
  assert.equal(resolveGuestChatRoute('chat-1', records).kind, 'known')
  assert.deepEqual(resolveGuestChatRoute('other-device-chat', records), {
    kind: 'unknown',
    chatId: 'other-device-chat',
  })
  assert.equal(toChatPath('chat-1'), '/chat/chat-1')
})

test('게스트 토큰은 새 대화와 메시지 갱신을 거쳐 저장되고 기존 무토큰 대화도 안전하게 로드된다', () => {
  const created = upsertGuestChat(
    [{ id: 'legacy-chat', title: '기존 대화' }],
    { id: 'chat-1', title: '장학 상담', guestToken: 'protected-token' },
    '2026-07-29T00:00:00Z',
  )
  const updated = updateGuestChatMessages(
    created,
    'chat-1',
    [question, latestAnswer],
    '2026-07-29T00:01:00Z',
  )
  const reloaded = parseGuestChatRecords(JSON.stringify(updated))

  assert.equal(reloaded[0].guestToken, 'protected-token')
  assert.deepEqual(reloaded[0].messages, [question, latestAnswer])
  assert.equal('guestToken' in reloaded[1], false)
})

test('게스트 토큰 헤더는 토큰이 있을 때만 추가된다', () => {
  assert.deepEqual(withGuestTokenHeader({ Accept: 'application/json' }), {
    Accept: 'application/json',
  })
  assert.deepEqual(withGuestTokenHeader({}, 'protected-token'), {
    [GUEST_TOKEN_HEADER]: 'protected-token',
  })
})

test('storage 읽기가 차단되면 게스트 목록 대신 빈 배열을 반환한다', () => {
  const throwingStorage = {
    getItem() {
      throw new Error('storage disabled')
    },
    setItem() {},
  }

  assert.deepEqual(readGuestChatRecords(throwingStorage), [])
})

test('storage 쓰기는 quota 예외를 false로 바꾸고 성공 시 true를 반환한다', () => {
  const throwingStorage = {
    getItem() {
      return null
    },
    setItem() {
      throw new Error('quota exceeded')
    },
  }
  let stored = ''
  const workingStorage = {
    getItem() {
      return stored
    },
    setItem(_key, value) {
      stored = value
    },
  }

  assert.equal(writeGuestChatRecords(throwingStorage, [{ id: 'chat-1' }]), false)
  assert.equal(writeGuestChatRecords(workingStorage, [{ id: 'chat-1' }]), true)
  assert.equal(stored, JSON.stringify([{ id: 'chat-1' }]))
})

test('게스트 메시지 갱신도 연속 assistant 최신값으로 정규화한다', () => {
  const records = [{ id: 'chat-1', title: '장학 상담' }]
  const updated = updateGuestChatMessages(
    records,
    'chat-1',
    [question, firstAnswer, latestAnswer],
    '2026-07-19T01:00:00Z',
  )

  assert.deepEqual(updated[0].messages, [question, latestAnswer])
  assert.equal(updated[0].updatedAt, '2026-07-19T01:00:00Z')
})

test('재생성은 질문을 추가하지 않고 기존 assistant 슬롯만 비운다', () => {
  const prepared = prepareRegeneration(
    [question, latestAnswer],
    latestAnswer.id,
    '2026-07-19T02:00:00Z',
  )

  assert.ok(prepared)
  assert.equal(prepared.messages.length, 2)
  assert.equal(prepared.question.id, question.id)
  assert.equal(prepared.assistant.id, latestAnswer.id)
  assert.equal(prepared.assistant.content, '')
  assert.equal(prepared.messages.filter((message) => message.isAsk).length, 1)
})

test('검증 상태에 따른 안내는 신규 상태를 우선하고 이전 기록은 grounded를 따른다', () => {
  for (const grounded of [true, false, undefined]) {
    assert.equal(getVerificationNoteKind({ verificationStatus: 'passed', grounded }), null)
    assert.equal(getVerificationNoteKind({ verificationStatus: 'not_required', grounded }), null)
    assert.equal(getVerificationNoteKind({ verificationStatus: 'failed', grounded }), 'failed')
    assert.equal(getVerificationNoteKind({ verificationStatus: 'unavailable', grounded }), 'unavailable')
  }
  assert.equal(getVerificationNoteKind({ grounded: false }), 'failed')
  assert.equal(getVerificationNoteKind({ grounded: true }), null)
  assert.equal(getVerificationNoteKind({}), null)
})

test('재생성·중단 시 이전 답변의 완료 상태를 지운다', () => {
  const verified = { ...latestAnswer, verificationStatus: 'unavailable', relevanceScore: 0.4,
    retrievalMode: 'sparse_degraded', degradedDatasets: ['courses'] }
  const regenerated = prepareRegeneration([question, verified], verified.id)
  assert.equal(regenerated.assistant.verificationStatus, undefined)
  assert.equal(regenerated.assistant.relevanceScore, undefined)
  assert.equal(regenerated.assistant.retrievalMode, undefined)
  assert.equal(regenerated.assistant.degradedDatasets, undefined)
  const stopped = finalizeStoppedAssistant([question, verified], verified.id)[1]
  assert.equal(stopped.verificationStatus, undefined)
  assert.equal(stopped.relevanceScore, undefined)
  assert.equal(stopped.retrievalMode, undefined)
  assert.equal(stopped.degradedDatasets, undefined)
})

test('스트림 중단은 임시 상태로 표시하되 완료 답변 메타데이터를 제거한다', () => {
  const partial = {
    ...latestAnswer,
    content: '받은 부분',
    requestId: 'request-1',
    sources: [{ sourceRef: 'source-1' }],
    suggestedQuestions: ['후속 질문'],
  }
  const stoppedPartial = finalizeStoppedAssistant([question, partial], partial.id)[1]
  assert.equal(stoppedPartial.content, '받은 부분')
  assert.equal(stoppedPartial.streamState, 'stopped')
  assert.equal(stoppedPartial.requestId, undefined)
  assert.deepEqual(stoppedPartial.sources, [])
  assert.deepEqual(stoppedPartial.suggestedQuestions, [])

  const empty = { ...latestAnswer, content: '' }
  const stoppedEmpty = finalizeStoppedAssistant([question, empty], empty.id)[1]
  assert.equal(stoppedEmpty.content, '답변 생성을 중단했습니다.')
  assert.equal(stoppedEmpty.streamState, 'stopped')
})

test('중단된 assistant 시도는 게스트 저장소에 완료 답변으로 남기지 않는다', () => {
  const stopped = finalizeStoppedAssistant(
    [question, { ...latestAnswer, content: '' }],
    latestAnswer.id,
  )[1]
  const updated = updateGuestChatMessages(
    [{ id: 'chat-1', title: '장학 상담' }],
    'chat-1',
    [question, stopped],
    '2026-07-19T03:00:00Z',
  )

  assert.deepEqual(updated[0].messages, [question])
})

test('이전 빌드가 저장한 중단 문구도 로드할 때 제거한다', () => {
  const records = parseGuestChatRecords(JSON.stringify([{
    id: 'chat-1',
    messages: [question, { ...latestAnswer, content: '답변 생성을 중단했습니다.' }],
  }]))

  assert.deepEqual(records[0].messages, [question])
})
