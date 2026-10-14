import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// A native cookie authenticates ordinary JSON requests; WKWebView has no cookie.
// Register the iOS bridge before Capacitor registers its HTTP plugin proxy.
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
const { Capacitor } = await import('@capacitor/core')

const payload = {
  id: 'f27c781b-e75a-41ea-a6a6-f4c6d7d88171',
  chatId: 'b4c3b384-ce03-4fef-9ea8-710fb97e9563',
  content: '겨울 계절학기 신청 기간 알려줘',
  createdTime: '2026-10-06T14:40:00Z',
}
const requestBody = { ...payload, isAsk: true }
const event = (type, fields = {}) => `data: ${JSON.stringify({ type, ...fields })}\n\n`
const completedResponse = () => new Response(
  event('text', { content: '공식 일정 답변' })
  + event('completion', { request_id: 'request-1', verification_status: 'not_required' })
  + event('done'),
  { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
)
const deferred = () => {
  let resolve
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

test('native SSE authenticates with a per-request ticket while preserving cookie and guest paths', async (t) => {
  const server = await createServer({
    configFile: false,
    root: new URL('../', import.meta.url).pathname,
    envDir: false,
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('https://api.example.test') },
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  })
  const originalFetch = globalThis.fetch
  try {
    const { useChatStream } = await server.ssrLoadModule('/src/hooks/useChatStream.ts')
    const getHook = () => {
      let hook
      renderToStaticMarkup(React.createElement(() => {
        hook = useChatStream()
        return null
      }))
      return hook
    }
    const resetBridge = (response = { status: 200, data: { ticket: 'one-use-ticket', expiresAt: '2026-10-06T14:41:00Z' } }) => {
      bridgeCalls.length = 0
      nativeRequest = async () => response
    }

    await t.test('native cookie issues a ticket and cookie-less fetch delivers the complete SSE answer', async () => {
      resetBridge()
      const fetchCalls = []
      globalThis.fetch = async (url, init) => {
        fetchCalls.push([url, init])
        // Reproduces the failing native browser jar instead of assuming cookies.
        if (init.headers['X-Dongttok-Stream-Ticket'] !== 'one-use-ticket') return new Response('', { status: 401 })
        return completedResponse()
      }
      const result = await getHook().streamMessage(payload, { onText: () => {} })
      assert.equal(result.answer, '공식 일정 답변')
      assert.equal(bridgeCalls.length, 1)
      const [plugin, method, ticketRequest] = bridgeCalls[0]
      assert.equal(plugin, 'CapacitorHttp')
      assert.equal(method, 'request')
      assert.equal(ticketRequest.url, 'https://api.example.test/chat/stream-ticket')
      assert.equal(ticketRequest.method, 'POST')
      assert.deepEqual(JSON.parse(ticketRequest.data), requestBody)
      assert.equal(ticketRequest.headers.Authorization, undefined)
      assert.equal(fetchCalls.length, 1)
      const [url, init] = fetchCalls[0]
      assert.equal(url, 'https://api.example.test/chat/stream')
      assert.equal(init.headers['X-Dongttok-Stream-Ticket'], 'one-use-ticket')
      assert.equal(init.headers.Authorization, undefined)
      assert.equal(init.headers['X-Guest-Token'], undefined)
      assert.equal(init.credentials, 'include')
      assert.deepEqual(JSON.parse(init.body), requestBody)
      assert.equal(init.body.includes('one-use-ticket'), false)
    })

    await t.test('native guest keeps the existing signed guest header without a ticket request', async () => {
      resetBridge()
      const fetchCalls = []
      globalThis.fetch = async (url, init) => {
        fetchCalls.push([url, init])
        return completedResponse()
      }
      await getHook().streamMessage({ ...payload, guestToken: 'signed-guest' }, { onText: () => {} })
      assert.equal(bridgeCalls.length, 0)
      assert.equal(fetchCalls.length, 1)
      assert.equal(fetchCalls[0][1].headers['X-Guest-Token'], 'signed-guest')
      assert.equal(fetchCalls[0][1].headers['X-Dongttok-Stream-Ticket'], undefined)
    })

    await t.test('ordinary web keeps cookie-based streaming without requesting a native ticket', async (webTest) => {
      webTest.mock.method(Capacitor, 'isNativePlatform', () => false)
      resetBridge()
      const fetchCalls = []
      globalThis.fetch = async (url, init) => {
        fetchCalls.push([url, init])
        return completedResponse()
      }
      await getHook().streamMessage(payload, { onText: () => {} })
      assert.equal(bridgeCalls.length, 0)
      assert.equal(fetchCalls.length, 1)
      assert.equal(fetchCalls[0][1].credentials, 'include')
      assert.equal(fetchCalls[0][1].headers['X-Dongttok-Stream-Ticket'], undefined)
    })

    await t.test('every regeneration obtains a new ticket and never retries a question automatically', async () => {
      resetBridge()
      nativeRequest = async () => ({ status: 200, data: { ticket: `ticket-${bridgeCalls.length}` } })
      const usedTickets = []
      globalThis.fetch = async (_url, init) => {
        usedTickets.push(init.headers['X-Dongttok-Stream-Ticket'])
        return completedResponse()
      }
      const hook = getHook()
      await hook.streamMessage(payload, { onText: () => {} })
      await hook.streamMessage(payload, { onText: () => {} })
      assert.equal(bridgeCalls.length, 2)
      assert.deepEqual(usedTickets, ['ticket-1', 'ticket-2'])
    })

    await t.test('abort during native ticket acquisition settles promptly and ignores the late response', async () => {
      resetBridge()
      const pending = deferred()
      nativeRequest = () => pending.promise
      let fetchCount = 0
      globalThis.fetch = async () => { fetchCount += 1; return completedResponse() }
      const hook = getHook()
      const stream = hook.streamMessage(payload, { onText: () => {} })
      const rejection = assert.rejects(stream, { name: 'AbortError' })
      await new Promise(setImmediate)
      hook.stopStream()
      await rejection
      pending.resolve({ status: 200, data: { ticket: 'late-ticket' } })
      await new Promise(setImmediate)
      assert.equal(fetchCount, 0)
    })

    await t.test('ticket failure never downgrades to unauthenticated or guest SSE', async () => {
      resetBridge({ status: 401, data: { message: '다시 로그인해 주세요.' } })
      let fetchCount = 0
      globalThis.fetch = async () => { fetchCount += 1; return completedResponse() }
      await assert.rejects(getHook().streamMessage(payload, { onText: () => {} }), { status: 401 })
      assert.equal(fetchCount, 0)
      assert.equal(bridgeCalls.length, 1)
    })

    await t.test('SSE authorization rejection exposes its status without retrying a consumed ticket', async () => {
      resetBridge()
      let fetchCount = 0
      globalThis.fetch = async () => { fetchCount += 1; return new Response('', { status: 401 }) }
      await assert.rejects(getHook().streamMessage(payload, { onText: () => {} }), { status: 401 })
      assert.equal(fetchCount, 1)
      assert.equal(bridgeCalls.length, 1)
    })

    await t.test('a new question aborts the earlier ticket wait and a late ticket cannot open its stream', async () => {
      resetBridge()
      const firstTicket = deferred()
      nativeRequest = () => bridgeCalls.length === 1
        ? firstTicket.promise
        : Promise.resolve({ status: 200, data: { ticket: 'second-question-ticket' } })
      const streamedQuestions = []
      globalThis.fetch = async (_url, init) => {
        streamedQuestions.push(JSON.parse(init.body).id)
        return completedResponse()
      }
      const hook = getHook()
      const firstStream = hook.streamMessage(payload, { onText: () => {} })
      const firstRejected = assert.rejects(firstStream, { name: 'AbortError' })
      await new Promise(setImmediate)
      const secondId = 'b7bd894a-e056-42bc-a7b2-d04bb2077004'
      await hook.streamMessage({ ...payload, id: secondId }, { onText: () => {} })
      await firstRejected
      firstTicket.resolve({ status: 200, data: { ticket: 'abandoned-ticket' } })
      await new Promise(setImmediate)
      assert.deepEqual(streamedQuestions, [secondId])
      assert.equal(bridgeCalls.length, 2)
    })

    await t.test('missing ticket rejects before a stream request can send credentials elsewhere', async () => {
      resetBridge({ status: 200, data: {} })
      let fetchCount = 0
      globalThis.fetch = async () => { fetchCount += 1; return completedResponse() }
      await assert.rejects(getHook().streamMessage(payload, { onText: () => {} }))
      assert.equal(fetchCount, 0)
    })

    await t.test('SSE partial text reaches the UI before the response and completion finish', async () => {
      resetBridge()
      let streamController
      globalThis.fetch = async () => new Response(new ReadableStream({
        start(controller) { streamController = controller },
      }), { status: 200 })
      const answers = []
      const stream = getHook().streamMessage(payload, { onText: (text) => answers.push(text) })
      await new Promise(setImmediate)
      streamController.enqueue(new TextEncoder().encode(event('text', { content: '부분 답변' })))
      await new Promise((resolve) => setTimeout(resolve, 60))
      assert.deepEqual(answers, ['부분 답변'])
      streamController.enqueue(new TextEncoder().encode(event('text', { content: ' 완성' }) + event('completion') + event('done')))
      streamController.close()
      assert.equal((await stream).answer, '부분 답변 완성')
      assert.equal(bridgeCalls.length, 1)
    })
  } finally {
    globalThis.fetch = originalFetch
    await server.close()
  }
})
