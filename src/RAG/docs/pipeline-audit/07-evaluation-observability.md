# 07. 평가·관측·릴리스 게이트

## 목표와 필요성

이 계층은 **반드시 필요**하다. 현재는 기능별 테스트와 평가 스크립트가 많지만, 운영 로그가 오래됐고 사람 라벨이 부족하며 실제 후보 서버 평가는 쉽게 건너뛸 수 있다. “테스트가 있다”에서 “나쁜 변경은 배포되지 않는다”로 바꿔야 한다.

주요 구현은 [tests](../../tests), [평가 스크립트](../../scripts), [ci.yml](../../../../.github/workflows/ci.yml), [rag-golden-release.yml](../../../../.github/workflows/rag-golden-release.yml)에 있다.

## 현재 검증 결과

| 검사 | 결과 | 해석 |
|---|---|---|
| RAG 전체 pytest | 889 passed, 3 failed, 9 warnings | 질의 분석 관련 회귀로 release blocker |
| 온톨로지 관련 선별 테스트 | 56 passed | 구현 단위는 안정적이나 운영 효용과 별개 |
| notices 품질 보고서 | gate 통과, mismatch 20/6,055 | 공지 내용 품질은 허용 범위, dense 오류는 별도 |
| canonical lineage | 실패 | notices 검사 오류, courses mismatch ratio 1.0 |
| 프런트 단위 테스트 | 50 passed | CI workflow에서는 `npm test`를 실행하지 않음 |
| 프런트 lint/build | 통과 | CI와 동일 경로는 정상 |
| 백엔드 계약 실행 | 통과 | CI에서는 solution build만 하고 계약 executable을 실행하지 않음 |
| 골든 후보 평가 | URL secret이 없으면 skip | 배포 차단 게이트가 아님 |

RAG 실패 3건은 다음 테스트에서 관찰됐다.

- `test_analysis_hides_history_when_previous_topic_does_not_overlap`
- `test_successful_analysis_is_recorded`
- `test_collector_is_optional`

테스트 실행 후 현재 워크트리에 관련 수정이 존재할 수 있으므로, 수정 완료 판단은 새 커밋/깨끗한 CI에서 전체 suite를 다시 실행해 내려야 한다.

## 운영 관측의 현재 한계

- 질의 로그 마지막 시각은 2026-08-29이며 기준일과 23일 차이가 난다. 서비스 무트래픽인지, 로그 pipeline 중단인지 구분되지 않는다.
- 최근 기준 표본은 304건에 불과하며 현재 운영 상태를 대표하지 않는다.
- 피드백은 6건뿐이라 모델/검색 의사결정에 쓸 수 없다.
- 평균 지연은 있으나 p50/p95/p99, route별·provider별 tail latency가 없다.
- fallback reason은 기록되지만 `no_results` 242건, `date_filter_eliminated_all` 63건 같은 큰 집단을 검색 실패 원인으로 더 분해하지 않는다.
- grounding `unchecked`와 성공 의미가 섞여 품질 지표를 낙관적으로 보이게 할 수 있다.
- 원문 질문·답변 보존 범위가 넓어 개인정보/민감정보 retention 정책이 필요하다.

## SQL 우선 검색 검증 현황 (2026-09-23)

- `scripts/evaluate_structured_retrieval.py`는 현재 결정적 production 라우터와 일치하는 45개 구조형 qrels를 같은 검색·후보 선택 함수에 hybrid/SQL 우선으로 재생한다. 상위 3문서 Recall 0.896 → 0.938, nDCG 0.938 → 0.991, SQL 적용 45/45건, 구조 라벨 기준 recall 악화 0건이다. 이 결과로 실제 답변 품질을 판정하지 않는다.
- `scripts/evaluate_structured_history.py`는 다중 route·합성·시점 이동 로그를 제외하고 실제 단일 route 질문만 비교한다. 현재 로그에서 적격 고유 질문은 28건, SQL 적용 18건, 후보 변화 17건이다. 사람 라벨이 없어 개선률은 산정하지 않는다. 현재 코퍼스로 과거 질문을 재생한 결과이며 historical snapshot은 아니다.
- 변경된 문서 82행을 사람이 판정할 수 있도록 로컬 gitignored `artifacts/ontology_evaluations/*-structured-history/changed_documents_review.csv`를 생성했다. 질문은 기존 로컬 redact 규칙을 적용했지만 자유 텍스트가 남으므로 외부 공유 금지다. `relevance`, `relation_correct`, `review_note`를 채워야 실제 승격 판단을 할 수 있다.
- 이전 비교의 규정 실로그 1건 약 1.6초 이상치는 SQL 재정렬이 아니라 첫 데이터셋 로딩 비용이 SQL 쪽에만 포함된 측정 편향이었다. 문서 내부 재정렬은 별도 계측에서 약 6ms였다. 두 평가 스크립트는 이제 데이터셋별 첫 baseline shortlist(아티팩트/모델 초기화 포함)를 `cold_warmup_ms_by_dataset`으로 분리한 뒤 동일한 warm 조건에서 쌍을 비교한다. 구조형 45건의 warm p50/p95는 hybrid 후보 약 119/127ms, SQL 그래프+후보 약 75/130ms다. p95 개선은 아직 입증되지 않았고 실제 endpoint·동시 요청 지연은 별도 확인이 필요하다.

재현 명령:

```bash
cd src/RAG
HF_HUB_OFFLINE=1 .venv311/bin/python scripts/evaluate_structured_retrieval.py --as-of 2026-09-23
HF_HUB_OFFLINE=1 .venv311/bin/python scripts/evaluate_structured_history.py
```

## 기능 개선 작업

### P0

- CI에서 프런트 `npm test`와 백엔드 계약 executable을 실제 실행한다.
- RAG 전체 suite를 일반 DB와 빈 DB에서 실행하고, test order randomization 또는 반복 실행으로 전역 상태 오염을 찾는다.
- canonical lineage 검사를 artifact build와 배포 전 모두 필수로 한다.
- 실제 후보 URL이 없는 release workflow는 성공 skip이 아니라 배포 불가로 판정한다. PR의 일반 단위 테스트와 실제 release gate는 별도 workflow로 분리해도 된다.
- production telemetry heartbeat를 추가해 “질의 0건”과 “로그 수집 중단”을 구분한다.

### P1

- 평가를 네 층으로 고정한다.
  1. 결정적 unit/contract 테스트
  2. 정본-파생물 lineage 및 데이터 품질
  3. 사람 판정 retrieval qrels
  4. 실제 후보 endpoint의 end-to-end answer 평가
- 데이터셋, intent, 날짜 민감도, 후속 질문 여부로 골든셋을 층화한다.
- 위 검토 CSV의 변경 문서부터 사람 relevance·관계 정확성을 판정한다. 기존 로그는 28건뿐이므로 최근 실사용 관계형 질문을 추가 수집해 200건 목표를 채운다. 교과목 실사용 표본은 현재 0건이라 별도 모집이 필요하다.
- meals/schedule은 `as_of`와 fixture revision을 고정해 시간이 지나도 재현되게 한다.
- retrieval은 Recall@k, MRR, nDCG를, answer는 faithfulness, relevancy, citation precision/recall을 측정한다.
- 자동 judge 점수의 일부를 사람이 이중 판정하고 judge drift를 추적한다.
- UI 피드백 이유를 `부정확`, `오래됨`, `출처 부족`, `질문 무관`, `느림`으로 구조화하되 자유 텍스트는 선택으로 둔다.

### P2

- 변경 종류별 영향받는 골든 subset을 먼저 실행하고 nightly에 전체 matrix를 실행한다.
- baseline과 candidate의 bootstrap confidence interval을 계산해 작은 표본의 우연한 변화를 승격 근거로 쓰지 않는다.

## 성능 개선 작업

- request path에서는 최소 원시 이벤트만 기록하고 percentile·route 집계는 비동기 작업으로 계산한다.
- `created_at`, `route`, `fallback_reason`, `verification_status`, `corpus_revision` 기준 조회 인덱스를 실제 대시보드 query plan으로 검증한다.
- raw text가 필요 없는 지표는 별도 집계 테이블에 보존해 480MB 운영 SQLite의 장기 증가를 제한한다.
- 골든 평가는 동시성 1의 품질 기준 run과 운영 동시성의 부하 run을 분리한다. 품질 평가가 rate limit이나 경합의 영향을 받지 않게 한다.
- 평가 모델 호출도 token, cache hit, latency, retry를 기록해 서비스 변경 비용에 평가 비용까지 포함한다.
- dashboard query와 auto-FAQ 생성이 serving DB write lock을 오래 점유하지 않도록 read replica 또는 snapshot을 사용한다.

## 필수 대시보드

| 범주 | 핵심 지표 |
|---|---|
| 데이터 | dataset freshness, 수집 성공률, 0건/partial 연속 횟수, lineage mismatch |
| 검색 | route 정확도, Recall@10, no-result, degraded dense, filter elimination |
| 응답 | fallback, source count, verification status, grounded/relevance 분포 |
| 성능 | 단계별 p50/p95/p99, TTFB, 전체 완료, 취소율, timeout |
| 비용 | provider/model별 호출, input/output/cache token, 요청당 비용 |
| 제품 | 질문→완료, 출처 클릭, 추천질문 클릭, 피드백률, 재질문률 |

## 권장 릴리스 게이트

- 전체 test failure 0.
- canonical lineage 필수 데이터셋 violation 0.
- 골든셋 faithfulness 0.85 이상, relevancy 0.85 이상, context recall 0.85 이상.
- 데이터셋별 Recall@10 baseline 대비 비열화 없음.
- fallback 절대 증가 2%p 미만이며 고위험 slice 증가 0.
- 전체 p95와 요청당 비용이 승인된 예산 이내.
- candidate manifest에 코드 SHA, corpus revision, dense/FTS revision, 모델, 프롬프트 계약 버전이 포함됨.

## 개인정보와 보존

- 원문 질문/답변은 목적, 접근자, 암호화, 보존기간, 삭제 요청 처리 기준을 명시한다.
- 운영 대시보드는 raw text 대신 route/fallback/latency 같은 집계값을 기본 사용한다.
- 위기·건강·신원 관련 패턴은 원문 저장 전 redact 또는 별도 단기 보존 정책을 적용한다.
- 평가 artifact를 외부로 공유할 때 session/user 식별자와 자유 텍스트를 제거한다.

## 완료 조건

- release 후보가 필수 게이트를 하나라도 통과하지 못하면 자동 배포되지 않는다.
- 운영 로그 heartbeat와 질의 로그가 5분 내 갱신되고, 단절 시 경보가 발생한다.
- 최소 200개의 사람 판정 retrieval qrel과 200개의 answer 골든 케이스를 확보한다.
- 모든 성능/품질 지표를 corpus와 모델 revision으로 재현할 수 있다.
