import { isAbortError } from './chatState.ts'

/**
 * 추천 질문(`/chat/followups`) 요청의 수명을 관리한다.
 * 새 질문이 시작되거나 화면을 벗어나면 진행 중인 요청을 취소하고,
 * 취소된 요청의 결과·오류는 상태에 반영하지 않는다.
 */
export interface FollowupRequestTracker {
  /** 이전 요청을 취소하고 새 요청용 signal을 만든다. */
  begin: () => AbortSignal
  /** 진행 중인 요청을 취소한다. */
  cancel: () => void
}

export const createFollowupRequestTracker = (): FollowupRequestTracker => {
  let controller: AbortController | null = null

  return {
    begin: () => {
      controller?.abort()
      controller = new AbortController()
      return controller.signal
    },
    cancel: () => {
      controller?.abort()
      controller = null
    },
  }
}

export interface LoadFollowupsOptions {
  signal: AbortSignal
  fetchQuestions: (signal: AbortSignal) => Promise<{ questions?: string[] } | undefined>
  onQuestions: (questions: string[]) => void
  onError?: (error: unknown) => void
}

/**
 * 추천 질문을 가져온다. signal이 취소되면(응답이 이미 도착했더라도) onQuestions/onError를 호출하지 않는다.
 * native HTTP 경로처럼 signal을 무시하는 전송 계층에서도 취소 후 상태가 바뀌지 않도록 결과 시점에 다시 확인한다.
 */
export const loadFollowups = async ({
  signal,
  fetchQuestions,
  onQuestions,
  onError,
}: LoadFollowupsOptions): Promise<void> => {
  if (signal.aborted) return
  try {
    const response = await fetchQuestions(signal)
    if (signal.aborted) return
    onQuestions(Array.isArray(response?.questions) ? response.questions : [])
  } catch (error) {
    if (signal.aborted || isAbortError(error)) return
    onError?.(error)
  }
}
