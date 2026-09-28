import assert from 'node:assert/strict'
import test from 'node:test'

import { Capacitor } from '@capacitor/core'

import { apiFetch } from '../src/api/client.ts'
import { fetchFollowups } from '../src/chat/chatApi.ts'
import { loadFollowups } from '../src/chat/followups.ts'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const settlesPromptly = async (promise) => {
  let timer
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('abort did not settle promptly')), 500)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

test('fetchFollowups passes AbortSignal to web fetch and cancellation shows no suggestions or error', async (t) => {
  t.mock.method(Capacitor, 'isNativePlatform', () => false)
  const pending = deferred()
  const fetchMock = t.mock.method(globalThis, 'fetch', (_url, init) => {
    if (init.signal.aborted) return Promise.reject(new DOMException('already aborted', 'AbortError'))
    init.signal.addEventListener('abort', () => pending.reject(new DOMException('aborted', 'AbortError')), { once: true })
    return pending.promise
  })
  const controller = new AbortController()
  const questions = []
  const errors = []
  const load = loadFollowups({
    signal: controller.signal,
    fetchQuestions: (signal) => fetchFollowups('request-1', 'guest-1', signal),
    onQuestions: (items) => questions.push(items),
    onError: (error) => errors.push(error),
  })

  assert.equal(fetchMock.mock.callCount(), 1)
  const [url, init] = fetchMock.mock.calls[0].arguments
  assert.equal(url, '/chat/followups')
  assert.equal(init.signal, controller.signal)
  assert.equal(init.headers['X-Guest-Token'], 'guest-1')
  assert.deepEqual(JSON.parse(init.body), { requestId: 'request-1' })
  controller.abort()
  await settlesPromptly(load)
  assert.deepEqual(questions, [])
  assert.deepEqual(errors, [])
  await assert.rejects(fetchFollowups('request-2', undefined, controller.signal), { name: 'AbortError' })
})

test('web response body AbortError is propagated without a JSON parse error log', async (t) => {
  t.mock.method(Capacitor, 'isNativePlatform', () => false)
  t.mock.method(globalThis, 'fetch', async () => ({
    ok: true,
    text: () => Promise.reject(new DOMException('aborted while reading', 'AbortError')),
  }))
  const errorLog = t.mock.method(console, 'error', () => {})

  await assert.rejects(apiFetch('/chat/followups'), { name: 'AbortError' })
  assert.equal(errorLog.mock.callCount(), 0)
})
