import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { DOMImplementation } from '@xmldom/xmldom'
import { createServer } from 'vite'

test('훅이 completion 검증 상태를 전달하고 누락된 grounded를 성공으로 만들지 않는다', async () => {
  const server = await createServer({
    configFile: false,
    root: new URL('../', import.meta.url).pathname,
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  })
  const originalFetch = globalThis.fetch
  try {
    const { useChatStream } = await server.ssrLoadModule('/src/hooks/useChatStream.ts')
    let streamMessage
    renderToStaticMarkup(React.createElement(() => {
      streamMessage = useChatStream().streamMessage
      return null
    }))

    const payload = { id: 'q-1', chatId: 'chat-1', content: '질문', createdTime: '2026-09-28T00:00:00Z' }
    const cases = [
      { completion: { verification_status: 'passed', grounded: true, relevance_score: 0.8, retrieval_mode: 'hybrid', degraded_datasets: [] }, expectedGrounded: true, expectedUpdates: [true] },
      { completion: { verification_status: 'failed', grounded: false, relevance_score: 0.2 }, expectedGrounded: false, expectedUpdates: [false] },
      { completion: { verification_status: 'unavailable', grounded: null, relevance_score: null, retrieval_mode: 'sparse_degraded', degraded_datasets: ['courses'] }, expectedGrounded: undefined, expectedUpdates: [] },
      { completion: { verification_status: 'not_required', grounded: null, relevance_score: null, retrieval_mode: 'sparse_only', degraded_datasets: [] }, expectedGrounded: undefined, expectedUpdates: [] },
      { completion: { grounded: false }, expectedGrounded: false, expectedUpdates: [false] },
      { completion: {}, expectedGrounded: undefined, expectedUpdates: [] },
      { completion: {}, priorGrounded: false, expectedGrounded: false, expectedUpdates: [false] },
      { completion: { grounded: null }, priorGrounded: false, expectedGrounded: false, expectedUpdates: [false] },
      { completion: { verification_status: 'failed' }, expectedGrounded: false, expectedUpdates: [false] },
      { completion: { verification_status: 'failed', grounded: null }, expectedGrounded: false, expectedUpdates: [false] },
    ]
    for (const { completion, priorGrounded, expectedGrounded, expectedUpdates } of cases) {
      const events = [
        { type: 'grounding', score: 0.7 },
        ...(priorGrounded === undefined ? [] : [{ type: 'grounding', grounded: priorGrounded }]),
        { type: 'text', content: '답변' },
        { type: 'completion', request_id: 'request-1', ...completion },
        { type: 'done' },
      ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')
      const bytes = new TextEncoder().encode(events)
      globalThis.fetch = async () => new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(bytes.slice(0, 19))
          controller.enqueue(bytes.slice(19))
          controller.close()
        },
      }), { status: 200 })
      const metadata = []
      const groundings = []
      const result = await streamMessage(payload, {
        onText: () => {},
        onMetadata: (value) => metadata.push(value),
        onGrounding: (value) => groundings.push(value),
      })

      assert.equal(result.answer, '답변')
      assert.equal(result.requestId, 'request-1')
      assert.equal(result.grounded, expectedGrounded)
      assert.deepEqual(groundings.map((grounding) => grounding.grounded), expectedUpdates)
      assert.equal(metadata.at(-1).verificationStatus, completion.verification_status)
      assert.equal(metadata.at(-1).relevanceScore, completion.relevance_score)
      assert.equal(metadata.at(-1).retrievalMode, completion.retrieval_mode)
      assert.deepEqual(metadata.at(-1).degradedDatasets, completion.degraded_datasets)
    }
  } finally {
    globalThis.fetch = originalFetch
    await server.close()
  }
})

test('스트림 텍스트는 프레임별로 합치고 모든 종료 경로에서 남은 텍스트를 전달한다', async (t) => {
  const server = await createServer({
    configFile: false,
    root: new URL('../', import.meta.url).pathname,
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  })
  try {
    const { useChatStream } = await server.ssrLoadModule('/src/hooks/useChatStream.ts')
    let streamMessage
    let stopStream
    renderToStaticMarkup(React.createElement(() => {
      ({ streamMessage, stopStream } = useChatStream())
      return null
    }))

    const payload = { id: 'q-1', chatId: 'chat-1', content: '질문', createdTime: '2026-09-28T00:00:00Z' }
    const event = (type, fields = {}) => `data: ${JSON.stringify({ type, ...fields })}\n\n`
    const withFakeClock = async (runCase, useRaf = true) => {
      const globalNames = ['fetch', 'requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout']
      const original = new Map(globalNames.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
      const realSetTimeout = globalThis.setTimeout
      const realClearTimeout = globalThis.clearTimeout
      const frames = new Map()
      const timers = new Map()
      const fakeTimerHandles = new Set()
      let nextId = 0
      let streamController
      globalThis.requestAnimationFrame = useRaf ? (callback) => {
        frames.set(++nextId, callback)
        return nextId
      } : undefined
      globalThis.cancelAnimationFrame = (id) => frames.delete(id)
      globalThis.setTimeout = (callback, delay, ...args) => {
        if (delay !== 40) return realSetTimeout(callback, delay, ...args)
        const handle = Symbol('stream-delivery-timer')
        fakeTimerHandles.add(handle)
        timers.set(handle, () => callback(...args))
        return handle
      }
      globalThis.clearTimeout = (handle) => {
        if (fakeTimerHandles.delete(handle)) timers.delete(handle)
        else realClearTimeout(handle)
      }
      globalThis.fetch = async () => new Response(new ReadableStream({
        start(controller) { streamController = controller },
      }), { status: 200 })
      const send = async (events) => {
        streamController.enqueue(new TextEncoder().encode(events))
        await new Promise(setImmediate)
      }
      const fire = (callbacks) => {
        const pending = [...callbacks.values()]
        callbacks.clear()
        for (const callback of pending) callback()
      }
      try {
        await runCase({
          send,
          close: () => streamController.close(),
          fireFrames: () => fire(frames),
          fireTimers: () => fire(timers),
          frameCount: () => frames.size,
          timerCount: () => timers.size,
        })
      } finally {
        for (const [name, descriptor] of original) {
          if (descriptor) Object.defineProperty(globalThis, name, descriptor)
          else Reflect.deleteProperty(globalThis, name)
        }
      }
    }

    await t.test('많은 delta가 한 프레임에서 한 번만 전달되고 완료 시 최종 문자열이 정확하다', async () => {
      await withFakeClock(async ({ send, close, fireFrames, frameCount, timerCount }) => {
        const updates = []
        const resultPromise = streamMessage(payload, { onText: (answer) => updates.push(answer) })
        await new Promise(setImmediate)
        const parts = Array.from({ length: 100 }, (_, index) => `${index},`)
        await send(parts.map((part) => event('text', { content: part })).join(''))
        assert.deepEqual(updates, [])
        assert.equal(frameCount(), 1)
        assert.equal(timerCount(), 1)
        fireFrames()
        assert.deepEqual(updates, [parts.join('')])
        await send(event('text', { content: '끝' }) + event('completion', { request_id: 'request-1' }) + event('done'))
        close()
        const result = await resultPromise
        assert.deepEqual(updates, [parts.join(''), `${parts.join('')}끝`])
        assert.equal(result.answer, `${parts.join('')}끝`)
        assert.equal(frameCount(), 0)
        assert.equal(timerCount(), 0)
      })
    })

    await t.test('completion 자체가 대기 중인 텍스트를 즉시 전달한다', async () => {
      await withFakeClock(async ({ send, close, frameCount }) => {
        const updates = []
        const resultPromise = streamMessage(payload, { onText: (answer) => updates.push(answer) })
        await new Promise(setImmediate)
        await send(event('text', { content: '미완성' }))
        assert.deepEqual(updates, [])
        await send(event('completion'))
        assert.deepEqual(updates, ['미완성'])
        assert.equal(frameCount(), 0)
        await send(event('done'))
        close()
        assert.equal((await resultPromise).answer, '미완성')
      })
    })

    await t.test('rAF가 없는 환경에서는 40ms 타이머가 한 번 전달한다', async () => {
      await withFakeClock(async ({ send, close, fireTimers, timerCount }) => {
        const updates = []
        const resultPromise = streamMessage(payload, { onText: (answer) => updates.push(answer) })
        await new Promise(setImmediate)
        await send(event('text', { content: '가' }) + event('text', { content: '나' }))
        assert.equal(timerCount(), 1)
        fireTimers()
        assert.deepEqual(updates, ['가나'])
        await send(event('completion') + event('done'))
        close()
        assert.equal((await resultPromise).answer, '가나')
      }, false)
    })

    await t.test('error와 abort는 대기 중인 부분 답변을 잃지 않는다', async () => {
      await withFakeClock(async ({ send }) => {
        const updates = []
        const resultPromise = streamMessage(payload, { onText: (answer) => updates.push(answer) })
        const rejected = assert.rejects(resultPromise, /연결 실패/)
        await new Promise(setImmediate)
        await send(event('text', { content: '부분 답변' }) + event('error', { message: '연결 실패' }))
        await rejected
        assert.deepEqual(updates, ['부분 답변'])
      })
      await withFakeClock(async ({ send, frameCount, timerCount }) => {
        const updates = []
        const resultPromise = streamMessage(payload, { onText: (answer) => updates.push(answer) })
        const rejected = assert.rejects(resultPromise, { name: 'AbortError' })
        await new Promise(setImmediate)
        await send(event('text', { content: '중단 전' }))
        stopStream()
        assert.deepEqual(updates, ['중단 전'])
        assert.equal(frameCount(), 0)
        assert.equal(timerCount(), 0)
        await rejected
      })
    })
  } finally {
    await server.close()
  }
})

test('새 대화 경로로 이동해도 같은 대화의 스트림을 유지하고 다른 대화로 이동하면 중단한다', async () => {
  const server = await createServer({
    configFile: false,
    root: new URL('../', import.meta.url).pathname,
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  })
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const document = new DOMImplementation().createDocument('http://www.w3.org/1999/xhtml', 'html')
  const window = { document, HTMLIFrameElement: class {} }
  document.defaultView = window
  document.addEventListener = () => {}
  document.removeEventListener = () => {}
  globalThis.window = window
  globalThis.document = document
  let root
  try {
    const { useChatStream } = await server.ssrLoadModule('/src/hooks/useChatStream.ts')
    let streamMessage
    const Harness = ({ routeChatId }) => {
      ({ streamMessage } = useChatStream(routeChatId))
      return null
    }
    const container = document.createElement('div')
    container.addEventListener = () => {}
    container.removeEventListener = () => {}
    root = createRoot(container)
    const renderRoute = (routeChatId) => flushSync(() => root.render(React.createElement(Harness, { routeChatId })))
    renderRoute(undefined)

    const streams = []
    globalThis.fetch = async (_url, options) => new Response(new ReadableStream({
      start(controller) { streams.push({ controller, signal: options.signal }) },
    }), { status: 200 })
    const payload = { id: 'q-1', chatId: 'new-chat', content: '이번 달 학사일정 알려줘', createdTime: '2026-10-02T00:00:00Z' }
    const firstResult = streamMessage(payload, { onText: () => {} })
    await new Promise(setImmediate)

    // / 에서 새 방 생성 직후 /chat/new-chat 으로 전환해도 진행 중인 요청은 같은 방에 속한다.
    renderRoute('new-chat')
    assert.equal(streams[0].signal.aborted, false)
    streams[0].controller.enqueue(new TextEncoder().encode([
      { type: 'text', content: '학사일정 답변' },
      { type: 'completion', request_id: 'request-1' },
      { type: 'done' },
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')))
    streams[0].controller.close()
    assert.equal((await firstResult).answer, '학사일정 답변')

    const secondResult = streamMessage({ ...payload, id: 'q-2' }, { onText: () => {} })
    const secondRejected = assert.rejects(secondResult, { name: 'AbortError' })
    await new Promise(setImmediate)
    renderRoute('another-chat')
    assert.equal(streams[1].signal.aborted, true)
    await secondRejected

    const thirdResult = streamMessage({ ...payload, id: 'q-3', chatId: 'another-chat' }, { onText: () => {} })
    const thirdRejected = assert.rejects(thirdResult, { name: 'AbortError' })
    await new Promise(setImmediate)
    flushSync(() => root.unmount())
    root = null
    assert.equal(streams[2].signal.aborted, true)
    await thirdRejected
  } finally {
    if (root) flushSync(() => root.unmount())
    globalThis.fetch = originalFetch
    await new Promise((resolve) => setTimeout(resolve, 50))
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    await server.close()
  }
})
