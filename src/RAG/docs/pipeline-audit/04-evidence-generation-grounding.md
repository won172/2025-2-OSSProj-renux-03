# 04. 근거 선택·생성·근거성 검사

## 목표와 필요성

검색 결과를 그대로 LLM에 넣는 것만으로는 충돌하는 공지, 다른 연도의 규정, 질문과 무관한 최신 문서를 안전하게 걸러내기 어렵다. 근거 선택과 검증은 **필요**하지만 모든 답변에 LLM 호출 2회를 추가하는 현재 방식은 비용과 지연이 크다. 결정적 검증과 위험 기반 LLM 검증의 혼합이 적합하다.

주요 구현은 [evidence_selector.py](../../src/services/evidence_selector.py), [langchain_chat.py](../../src/services/langchain_chat.py), [grounding.py](../../src/services/grounding.py), [rag_service.py](../../api/rag_service.py)에 있다.

## 현재 상태

- 최신 공지, 현재 유효 공지, 날짜가 지정된 일정은 결정적 근거 선택을 사용한다.
- 그 외에는 OpenAI 기반 evidence selector가 관련 문서를 그룹화하고, 실패 시 lexical fallback을 사용한다.
- 선택된 컨텍스트는 최대 길이 예산 안에서 생성 모델에 전달된다.
- 생성 후 grounding과 질문 관련성을 LLM으로 검사한다.
- 최근 실트래픽 조건부 평균은 근거 선택 1.31초, 생성 4.00초, 근거성 검사 1.95초다.
- 검사된 응답의 78.7%가 grounded 판정을 받았다.
- `check_answer_grounding`의 빈 입력 또는 예외 결과는 `checked=False`이면서 `grounded=True`, `score=1.0`이다. 미검사를 성공처럼 표현할 위험이 있다.
- 기본 설정은 grounding이 끝나기 전에 토큰을 스트리밍한다. 사후 실패 시 영구 저장은 막을 수 있어도 사용자가 이미 본 텍스트는 회수할 수 없다.

## 기능 개선 작업

### P0

- 검증 결과를 `passed`, `failed`, `unavailable`, `not_required` 네 상태로 바꾼다. `unavailable`을 `grounded=true`로 직렬화하지 않는다.
- completion 계약에 `verification_status`, `grounding_score`, `relevance_score`, `failure_policy`를 포함하고 백엔드/클라이언트가 보존한다.
- 학칙, 금액, 마감, 자격 요건처럼 행동에 영향을 주는 답변은 검증 완료 전까지 buffer하거나, 결정적 직접 응답으로만 제공한다.
- grounding provider 장애 시 정책을 route별로 정한다. 고위험 route는 안전한 대체 응답, 저위험 route는 “검증 불가” 표기와 출처 중심 응답을 사용한다.

### P1

- 단일 고신뢰 문서, 전화번호, 과목코드, 날짜/금액 field는 결정적 selector와 claim checker를 사용한다.
- LLM selector는 여러 문서의 결합, 문서 간 충돌, 표/본문 연결이 필요한 경우에만 호출한다.
- 답변을 먼저 `claims[]` 구조로 만들고 각 claim에 `source_ref`를 연결한 뒤 최종 텍스트를 렌더링한다.
- 출처 간 날짜나 규칙이 충돌하면 최신 문서를 조용히 선택하지 말고 차이를 사용자에게 설명한다.
- 컨텍스트에는 중복 청크 전체 대신 문서별 핵심 구간과 구조화 메타데이터를 넣는다.
- 추천 질문 생성은 현재처럼 본 응답 완료 후 비동기로 유지하되, source lineage를 만족하는 항목만 노출한다.

### P2

- 결정적 claim checker와 LLM judge의 불일치 표본을 사람이 판정해 자동 검증 범위를 점진적으로 확대한다.
- 생성 모델/프롬프트 변경은 faithfulness, relevancy, latency, token cost 네 축의 Pareto 비교로 결정한다.

## 성능 개선 작업

1. 직접 응답과 단일 문서 답변은 selector와 grounding LLM을 모두 생략한다.
2. evidence selector와 grounding 입력에서 중복 문구를 제거하고 문서별 문자/token budget을 둔다.
3. 최대 출력 길이와 응답 형식을 intent별로 제한한다. 연락처나 일정 질문에 장문의 생성 예산을 주지 않는다.
4. provider 호출에 단계별 timeout과 취소 전파를 적용한다.
5. 모델 prompt cache 사용률과 실제 절감 token을 기록한다.
6. 저위험 스트리밍은 유지하고, 고위험 답변만 선택적으로 buffer해 TTFB와 안전성을 함께 관리한다.

## 권장 정책

| 답변 유형 | selector | 생성 | 검증 | 스트리밍 |
|---|---|---|---|---|
| 급식·일정·전화번호 typed 응답 | 결정적 | 템플릿 | 결정적 | 즉시 |
| 단일 공지 요약 | 결정적 | LLM | claim check + 필요 시 LLM | 저위험만 즉시 |
| 복수 규정/공지 종합 | LLM | LLM | LLM + 출처 연결 | 검증 후 |
| 검색 없음/데이터 stale | 없음 | 고정 문구 | not_required | 즉시 |
| 검증기 장애의 고위험 답변 | 정책 기반 최소 근거 | 제한 | unavailable | 대체 응답 |

## 완료 조건

- 미검사와 검증 성공이 API, DB, UI에서 구분된다.
- 모든 사실 claim이 최소 하나의 전송된 `source_ref`에 연결된다.
- 고위험 route에서 검증 실패 답변의 사전 노출이 0건이다.
- 골든셋 faithfulness 0.85 이상, answer relevancy 0.85 이상을 유지한다.
- 직접/단일 문서 경로 최적화 후 전체 LLM 호출 수와 p95가 줄고, 품질 비열화가 없다.
