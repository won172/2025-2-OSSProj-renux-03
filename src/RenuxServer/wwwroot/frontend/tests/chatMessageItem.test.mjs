import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('답변 카드가 검증 실패·불가를 구분하고 이전 기록의 경고를 유지한다', async () => {
  const server = await createServer({
    configFile: false,
    root: new URL('../', import.meta.url).pathname,
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  })
  try {
    const { default: ChatMessageItem } = await server.ssrLoadModule('/src/components/chat/ChatMessageItem.tsx')
    const render = (fields) => renderToStaticMarkup(React.createElement(ChatMessageItem, {
      message: {
        id: 'answer-1', chatId: 'chat-1', isAsk: false,
        content: '확인 결과', createdTime: '2026-09-28T00:00:00Z', ...fields,
      },
      isStopped: false,
      isLastAssistant: false,
      canRegenerate: false,
      showScores: false,
      busy: false,
      activeCitationNumber: null,
      onCitationClick: () => {},
      onRegenerate: () => {},
      onSelectSuggestion: () => {},
    }))

    const unavailable = render({ verificationStatus: 'unavailable', grounded: undefined })
    assert.match(unavailable, /role="status"[^>]*>근거 확인을 완료하지 못한 답변입니다/)
    assert.match(unavailable, /ch-note--muted/)
    assert.doesNotMatch(unavailable, /충분히 확인되지 않은 내용/)

    for (const fields of [{ verificationStatus: 'failed' }, { grounded: false }]) {
      const failed = render(fields)
      assert.match(failed, /충분히 확인되지 않은 내용/)
      assert.match(failed, /ch-note--warn/)
      assert.doesNotMatch(failed, /근거 확인을 완료하지 못한 답변입니다/)
    }
    for (const fields of [
      { verificationStatus: 'passed', grounded: false },
      { verificationStatus: 'not_required', grounded: false },
      { grounded: true },
      {},
    ]) {
      const clean = render(fields)
      assert.doesNotMatch(clean, /근거 확인을 완료하지 못한 답변입니다|충분히 확인되지 않은 내용/)
    }

    const degraded = render({ retrievalMode: 'sparse_degraded', degradedDatasets: ['courses'] })
    assert.match(degraded, /role="note"[^>]*>일부 검색 기능이 제한된 상태에서 만든 답변입니다/)
    assert.doesNotMatch(degraded, /aria-live=/)
    const combined = render({ retrievalMode: 'sparse_degraded', isFallback: true,
      fallbackReason: 'stale_data', verificationStatus: 'unavailable' })
    assert.match(combined, /일부 검색 기능이 제한된 상태에서 만든 답변입니다/)
    assert.match(combined, /근거 확인을 완료하지 못한 답변입니다/)
    assert.match(combined, /오래된|최신/)
    for (const fields of [{ retrievalMode: 'hybrid' }, { retrievalMode: 'sparse_only' }, {}]) {
      const markup = render(fields)
      assert.doesNotMatch(markup, /일부 검색 기능이 제한된 상태에서 만든 답변입니다/)
    }
  } finally {
    await server.close()
  }
})
