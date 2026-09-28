/**
 * RAG 서버가 completion 메타데이터의 `fallback_reason`으로 보내는 값별 사용자 표시 문구.
 * 서버 정의: src/RAG/api/rag_service.py 의 FALLBACK_REASON_* 상수.
 * 서버에 새 사유가 추가되면 여기에도 문구를 추가한다. 모르는 값은 안전한 일반 문구로 표시한다.
 */
export const FALLBACK_REASON_LABELS: Readonly<Record<string, string>> = Object.freeze({
  no_results: '검색 결과 없음',
  date_filter_eliminated_all: '기간 조건에 맞는 자료 없음',
  active_deadline_filter_eliminated_all: '마감 전인 공지 없음',
  dataset_unavailable: '일시적으로 자료를 불러오지 못함',
  score_below_threshold: '관련 근거 부족',
  campus_out_of_scope: '서비스 범위 밖 캠퍼스',
  future_unannounced: '아직 발표되지 않은 정보',
  stale_data: '최신 자료 확인 필요',
  clarification_needed: '질문에 추가 조건 필요',
  out_of_domain: '지원 범위 밖 질문',
  selector_refused: '답변을 뒷받침할 근거를 선택하지 못함',
})

/** 사유가 없거나(클라이언트 연결 오류 등) 알 수 없는 사유일 때 쓰는 일반 문구. */
export const GENERIC_FALLBACK_LABEL = '제한된 답변'

export const getFallbackLabel = (reason?: string | null): string => {
  if (!reason) return GENERIC_FALLBACK_LABEL
  return Object.prototype.hasOwnProperty.call(FALLBACK_REASON_LABELS, reason)
    ? FALLBACK_REASON_LABELS[reason]
    : GENERIC_FALLBACK_LABEL
}
