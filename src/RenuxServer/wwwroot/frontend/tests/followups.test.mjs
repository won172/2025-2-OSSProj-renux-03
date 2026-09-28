import assert from 'node:assert/strict'
import test from 'node:test'

import { createFollowupRequestTracker, loadFollowups } from '../src/chat/followups.ts'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

test('새 요청 시작은 이전 추천 요청을 취소하고 새 signal을 전달한다', async () => {
  const tracker = createFollowupRequestTracker()
  const pending = deferred()
  const questions = []
  const errors = []
  const firstSignal = tracker.begin()
  const firstLoad = loadFollowups({
    signal: firstSignal,
    fetchQuestions: (signal) => {
      assert.equal(signal, firstSignal)
      return pending.promise
    },
    onQuestions: (items) => questions.push(items),
    onError: (error) => errors.push(error),
  })

  const secondSignal = tracker.begin()
  assert.equal(firstSignal.aborted, true)
  assert.equal(secondSignal.aborted, false)
  pending.resolve({ questions: ['지난 질문의 추천'] })
  await firstLoad
  assert.deepEqual(questions, [])
  assert.deepEqual(errors, [])

  await loadFollowups({
    signal: secondSignal,
    fetchQuestions: (signal) => {
      assert.equal(signal, secondSignal)
      return Promise.resolve({ questions: ['현재 질문의 추천'] })
    },
    onQuestions: (items) => questions.push(items),
  })
  assert.deepEqual(questions, [['현재 질문의 추천']])
})

test('화면 이탈로 취소한 추천 요청의 늦은 실패는 오류를 표시하지 않는다', async () => {
  const tracker = createFollowupRequestTracker()
  const pending = deferred()
  const questions = []
  const errors = []
  const signal = tracker.begin()
  const load = loadFollowups({
    signal,
    fetchQuestions: () => pending.promise,
    onQuestions: (items) => questions.push(items),
    onError: (error) => errors.push(error),
  })

  tracker.cancel()
  assert.equal(signal.aborted, true)
  pending.reject(new Error('late network failure'))
  await load
  assert.deepEqual(questions, [])
  assert.deepEqual(errors, [])
})

test('AbortError는 오류를 표시하지 않고 실제 실패는 오류 콜백으로 전달한다', async () => {
  const tracker = createFollowupRequestTracker()
  const errors = []
  const options = {
    signal: tracker.begin(),
    onQuestions: () => assert.fail('실패한 요청은 추천을 표시하면 안 된다'),
    onError: (error) => errors.push(error),
  }
  await loadFollowups({
    ...options,
    fetchQuestions: () => Promise.reject(new DOMException('aborted', 'AbortError')),
  })
  assert.deepEqual(errors, [])

  const failure = new Error('network failure')
  await loadFollowups({ ...options, fetchQuestions: () => Promise.reject(failure) })
  assert.deepEqual(errors, [failure])
})
