import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import React from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { DOMImplementation } from '@xmldom/xmldom'
import { createServer } from 'vite'

test('대화 내용은 live region이 아니고 종료 상태만 별도 영역에서 알린다', async () => {
  const server = await createServer({
    configFile: false,
    root: new URL('../', import.meta.url).pathname,
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'silent',
  })
  try {
    const { default: ChatStreamAnnouncement } =
      await server.ssrLoadModule('/src/components/chat/ChatStreamAnnouncement.tsx')
    const { default: ChatMessageItem } =
      await server.ssrLoadModule('/src/components/chat/ChatMessageItem.tsx')
    const { default: ChatComposer } =
      await server.ssrLoadModule('/src/components/chat/ChatComposer.tsx')
    const { createChatStreamAnnouncementTracker } =
      await server.ssrLoadModule('/src/chat/streamAnnouncement.ts')
    const render = (status) => renderToStaticMarkup(React.createElement(ChatStreamAnnouncement, { status }))
    assert.match(render(null), /role="status" aria-live="polite" aria-atomic="true"><\/p>/)
    assert.match(render('completed'), />답변 완료<\/p>/)
    assert.match(render('stopped'), />답변 중단<\/p>/)
    for (const status of [null, 'completed', 'stopped']) {
      assert.doesNotMatch(render(status), /tabIndex|autoFocus|\.focus\(/)
    }

    const homePage = await readFile(new URL('../src/pages/home/HomePage.tsx', import.meta.url), 'utf8')
    assert.match(homePage, /<ul className="ch-thread" aria-busy=\{chatSending\} aria-label="채팅 메시지">/)
    assert.doesNotMatch(homePage, /<ul className="ch-thread"[^>]*(?:aria-live|role="log")/)
    assert.match(homePage, /onStop=\{stopCurrentStream\}/)

    const renderStoppedThread = (status) => renderToStaticMarkup(React.createElement(React.Fragment, null,
      React.createElement('ul', { className: 'ch-thread' }, React.createElement(ChatMessageItem, {
        message: { id: 'partial-answer', chatId: 'chat-1', isAsk: false, content: '부분 답변', streamState: 'stopped' },
        isStopped: true,
        isLastAssistant: true,
        canRegenerate: false,
        showScores: false,
        busy: false,
        activeCitationNumber: null,
        onCitationClick: () => {},
        onRegenerate: () => {},
        onSelectSuggestion: () => {},
      })),
      React.createElement(ChatStreamAnnouncement, { status }),
    ))
    const tracker = createChatStreamAnnouncementTracker()
    const superseded = tracker.beginRequest()
    tracker.beginRequest()
    const supersededStatus = tracker.terminal(superseded, 'stopped')
    assert.equal(supersededStatus, null)
    const supersededThread = renderStoppedThread(supersededStatus)
    assert.match(supersededThread, /부분 답변/)
    assert.match(supersededThread, /생성을 중단한 임시 답변/)
    assert.doesNotMatch(supersededThread, /답변 중단|role="status"[^>]*>[^<]+<\/p>/)
    assert.equal((supersededThread.match(/role="status"/g) ?? []).length, 1)

    const userStopped = tracker.beginRequest()
    tracker.markUserStop()
    const stoppedStatus = tracker.terminal(userStopped, 'stopped')
    assert.equal(stoppedStatus, 'stopped')
    const stoppedThread = renderStoppedThread(stoppedStatus)
    assert.match(stoppedThread, /생성을 중단한 임시 답변/)
    assert.equal((stoppedThread.match(/role="status"/g) ?? []).length, 1)
    assert.match(stoppedThread, /role="status" aria-live="polite" aria-atomic="true">답변 중단<\/p>/)

    const failedRequest = tracker.beginRequest()
    const failedStatus = tracker.terminal(failedRequest, 'error')
    assert.equal(failedStatus, null)
    const failedThread = renderToStaticMarkup(React.createElement(React.Fragment, null,
      React.createElement(ChatStreamAnnouncement, { status: failedStatus }),
      React.createElement(ChatComposer, {
        inputRef: { current: null },
        value: '',
        onChange: () => {},
        onSubmit: () => {},
        onStop: () => {},
        sending: false,
        disabled: false,
        placeholder: '질문 입력',
        error: '메시지를 전송하지 못했습니다.',
      }),
    ))
    assert.equal((failedThread.match(/role="alert"/g) ?? []).length, 1)
    assert.match(failedThread, /role="alert">메시지를 전송하지 못했습니다.<\/span>/)
    assert.doesNotMatch(failedThread, /답변 오류|role="status"[^>]*>[^<]+<\/p>/)

    const originalWindow = globalThis.window
    const originalDocument = globalThis.document
    const document = new DOMImplementation().createDocument('http://www.w3.org/1999/xhtml', 'html')
    const window = { document, HTMLIFrameElement: class {} }
    document.defaultView = window
    document.addEventListener = () => {}
    document.removeEventListener = () => {}
    globalThis.window = window
    globalThis.document = document
    try {
      const createHarness = () => {
        const container = document.createElement('div')
        container.addEventListener = () => {}
        container.removeEventListener = () => {}
        const root = createRoot(container)
        const tracker = createChatStreamAnnouncementTracker()
        const announcements = []
        let lastText = ''
        let liveRegion = null
        const renderStatus = (status) => {
          flushSync(() => root.render(React.createElement(ChatStreamAnnouncement, { status })))
          if (liveRegion) assert.equal(container.firstChild, liveRegion)
          else liveRegion = container.firstChild
          const text = container.firstChild.textContent
          if (text && text !== lastText) announcements.push(text)
          lastText = text
          return text
        }
        renderStatus(null)
        return {
          announcements,
          start: () => {
            const request = tracker.beginRequest()
            renderStatus(null)
            return request
          },
          finish: (request, status) => {
            const next = tracker.terminal(request, status)
            if (next) renderStatus(next)
            return next
          },
          userStop: () => tracker.markUserStop(),
          text: () => container.firstChild.textContent,
          close: () => flushSync(() => root.unmount()),
        }
      }

      {
        const live = createHarness()
        try {
          const first = live.start()
          assert.equal(live.text(), '')
          assert.equal(live.finish(first, 'completed'), 'completed')
          assert.equal(live.finish(first, 'completed'), null)
          const second = live.start()
          assert.equal(live.text(), '')
          assert.equal(live.finish(second, 'completed'), 'completed')
          assert.deepEqual(live.announcements, ['답변 완료', '답변 완료'])
        } finally {
          live.close()
        }
      }

      {
        const live = createHarness()
        try {
          const superseded = live.start()
          const current = live.start()
          assert.equal(live.finish(superseded, 'stopped'), null)
          assert.equal(live.finish(superseded, 'error'), null)
          assert.equal(live.text(), '')
          assert.equal(live.finish(current, 'completed'), 'completed')
          assert.deepEqual(live.announcements, ['답변 완료'])
        } finally {
          live.close()
        }
      }

      {
        const live = createHarness()
        try {
          const abortedWithoutUserStop = live.start()
          assert.equal(live.finish(abortedWithoutUserStop, 'stopped'), null)
          assert.deepEqual(live.announcements, [])
          const stoppedByUser = live.start()
          live.userStop()
          assert.equal(live.finish(stoppedByUser, 'stopped'), 'stopped')
          assert.deepEqual(live.announcements, ['답변 중단'])
          const errored = live.start()
          assert.equal(live.finish(errored, 'error'), null)
          assert.equal(live.finish(errored, 'error'), null)
          assert.deepEqual(live.announcements, ['답변 중단'])
        } finally {
          live.close()
        }
      }
    } finally {
      await new Promise((resolve) => setTimeout(resolve, 50))
      globalThis.window = originalWindow
      globalThis.document = originalDocument
    }
  } finally {
    await server.close()
  }
})
