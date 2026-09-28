import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
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
      { completion: { verification_status: 'passed', grounded: true, relevance_score: 0.8 }, expectedGrounded: true, expectedUpdates: [true] },
      { completion: { verification_status: 'failed', grounded: false, relevance_score: 0.2 }, expectedGrounded: false, expectedUpdates: [false] },
      { completion: { verification_status: 'unavailable', grounded: null, relevance_score: null }, expectedGrounded: undefined, expectedUpdates: [] },
      { completion: { verification_status: 'not_required', grounded: null, relevance_score: null }, expectedGrounded: undefined, expectedUpdates: [] },
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
    }
  } finally {
    globalThis.fetch = originalFetch
    await server.close()
  }
})
