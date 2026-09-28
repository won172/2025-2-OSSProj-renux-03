import assert from 'node:assert/strict'
import test from 'node:test'

// Register a fake iOS bridge before @capacitor/core registers CapacitorHttp.
// The plugin proxy then calls nativePromise, the same branch used on device.
const bridgeCalls = []
let nativeRequest
globalThis.webkit = { messageHandlers: { bridge: {} } }
globalThis.Capacitor = {
  PluginHeaders: [{ name: 'CapacitorHttp', methods: [{ name: 'request', rtype: 'promise' }] }],
  nativePromise: (...args) => {
    bridgeCalls.push(args)
    return nativeRequest(...args)
  },
}

const { apiFetch } = await import('../src/api/client.ts')
const { loadFollowups } = await import('../src/chat/followups.ts')

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

test('native apiFetch aborts promptly and a late response cannot update follow-up state', async () => {
  const pending = deferred()
  nativeRequest = () => pending.promise
  bridgeCalls.length = 0
  const controller = new AbortController()
  const questions = []
  const errors = []
  const load = loadFollowups({
    signal: controller.signal,
    fetchQuestions: (signal) => apiFetch('https://example.test/chat/followups', {
      method: 'POST',
      json: { requestId: 'request-1' },
      signal,
    }),
    onQuestions: (items) => questions.push(items),
    onError: (error) => errors.push(error),
  })

  await Promise.resolve()
  assert.equal(bridgeCalls.length, 1)
  assert.equal(bridgeCalls[0][0], 'CapacitorHttp')
  assert.equal(bridgeCalls[0][1], 'request')
  assert.equal(bridgeCalls[0][2].url, 'https://example.test/chat/followups')
  controller.abort()
  await settlesPromptly(load)
  assert.deepEqual(questions, [])
  assert.deepEqual(errors, [])

  pending.resolve({ status: 200, data: { questions: ['late suggestion'] } })
  await Promise.resolve()
  assert.deepEqual(questions, [])
  assert.deepEqual(errors, [])

  const alreadyAborted = new AbortController()
  alreadyAborted.abort()
  await assert.rejects(apiFetch('https://example.test/chat/followups', {
    signal: alreadyAborted.signal,
  }), { name: 'AbortError' })
  assert.equal(bridgeCalls.length, 1)
})

test('native apiFetch rejects with AbortError before the bridge reports a late failure', async () => {
  const pending = deferred()
  nativeRequest = () => pending.promise
  const controller = new AbortController()
  const request = apiFetch('https://example.test/chat/followups', { signal: controller.signal })

  controller.abort()
  await assert.rejects(settlesPromptly(request), { name: 'AbortError' })
  pending.reject(new Error('native failure after cancellation'))
  await Promise.resolve()
})
