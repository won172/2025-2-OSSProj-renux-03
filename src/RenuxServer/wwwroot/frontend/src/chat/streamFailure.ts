const connectionFailureMessage = '응답 연결이 끊겼습니다. 다시 생성 버튼으로 시도해주세요.'

export const getChatStreamFailureMessage = (error: unknown, authenticated: boolean) => {
  const status = error && typeof error === 'object' && 'status' in error ? error.status : undefined
  if (status === 401) {
    return authenticated
      ? '로그인 세션을 확인할 수 없습니다. 다시 로그인한 뒤 질문해 주세요.'
      : '게스트 세션을 확인할 수 없습니다. 새 대화를 시작해 주세요.'
  }
  if (status === 403) return '이 대화에 접근할 수 없습니다. 대화를 다시 선택해 주세요.'
  return connectionFailureMessage
}

/** Keep already delivered text visible, with an explicit incomplete warning. */
export const getFailedAssistantContent = (partial: string, failureMessage: string) =>
  partial.trim() ? `${partial}\n\n${failureMessage}` : failureMessage
