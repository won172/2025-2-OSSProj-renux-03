import assert from 'node:assert/strict'
import test from 'node:test'

import {
  FALLBACK_REASON_LABELS,
  GENERIC_FALLBACK_LABEL,
  getFallbackLabel,
} from '../src/chat/fallbackLabels.ts'

test('RAG의 fallback 사유를 각각 다른 한국어 문구로 표시한다', () => {
  const expected = {
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
  }
  const labels = Object.entries(expected).map(([reason, label]) => {
    assert.equal(getFallbackLabel(reason), label)
    return label
  })

  assert.deepEqual(FALLBACK_REASON_LABELS, expected)
  assert.equal(new Set(labels).size, Object.keys(expected).length)
  for (const label of labels) {
    assert.match(label, /[가-힣]/)
    assert.notEqual(label, GENERIC_FALLBACK_LABEL)
    assert.notEqual(label, '근거 부족')
  }
})

test('알 수 없거나 빠진 사유는 안전한 일반 문구로 표시한다', () => {
  for (const reason of [undefined, null, '', 'new_server_reason', 'toString']) {
    assert.equal(getFallbackLabel(reason), GENERIC_FALLBACK_LABEL)
  }
})
