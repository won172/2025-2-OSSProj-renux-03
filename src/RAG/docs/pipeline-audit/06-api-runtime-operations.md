# 06. RAG API·런타임·운영

## 목표와 필요성

API 계층은 각 기능을 하나의 일관된 요청 수명주기로 묶고, 장애 시 안전하게 degraded 또는 not-ready 상태를 알려야 한다. 현재 readiness와 strict stream terminal contract의 방향은 좋지만, 단일 파일과 단일 프로세스에 역할이 과도하게 모여 있다.

주요 구현은 약 9,856줄의 [rag_service.py](../../api/rag_service.py), [config.py](../../src/config.py), [scheduler.py](../../src/services/scheduler.py), [docker-compose.yml](../../../../src/RenuxServer/docker-compose.yml)에 있다.

## 현재 상태

- `/ready`는 설정, DB 쓰기, 6개 데이터셋, canonical lineage, embedder를 필수 검사한다.
- canonical lineage가 현재 실패하므로 정상 startup 기준에서는 readiness도 실패하는 것이 맞다.
- dense 오류는 dataset check를 `loaded_degraded`로 표시하지만 canonical lineage가 별도 fail-closed 역할을 한다.
- 스케줄러는 readiness 비필수다. Compose에서는 활성화되지만 API 프로세스 안에서 실행된다.
- 스트리밍과 비스트리밍 요청 경로가 각각 길게 구현되어 로직 중복이 크다.
- 두 경로의 route·검색 전략·revision 확인·SQL 문서 근거를 정하는 부분은 공통 `_plan_retrieval`로 묶었다. 근거 선택·생성·fallback의 중복은 아직 남아 있다.
- SQLite 스키마는 startup `create_all`과 보완 ALTER에 의존하며 명시적 migration/version 체계가 약하다.
- FastAPI `on_event`와 Pydantic class config deprecation warning이 발생한다.
- RAG 서비스는 OpenAI 키를 필수 설정으로 요구한다. 답변 모델을 로컬로 바꿔도 분석·선택·검증 경로가 OpenAI 가용성에 결합된다.
- serving 프로세스에서 수집, 인덱스 reload, readiness 갱신까지 수행한다. 다중 replica로 확장하면 중복 scheduler 실행 위험이 있다.

## 기능 개선 작업

### P0

- readiness 실패 원인을 운영자가 즉시 조치할 수 있게 dataset, revision, collection pointer, last_success_at, recovery hint를 포함한다.
- scheduler 비정상을 서비스 전체 ready와 분리하되, freshness SLO를 넘긴 데이터셋은 해당 기능만 unavailable로 만든다.
- 인덱스 전환과 rollback runbook을 작성하고 admin endpoint는 인증·감사로그·maintenance lock을 필수로 한다.
- 설정 시작 검증에서 provider별 필수 키를 실제 활성 기능에 맞춰 계산한다. 사용하지 않는 provider의 키 때문에 부팅이 막히지 않게 한다.

### P1

- 공통 `execute_query(plan, mode)` 코어를 만들고 SSE와 JSON은 결과/event adapter만 다르게 한다.
- 위기, 직접 응답, 일반 RAG, fallback을 `QueryOutcome` 하나로 정규화해 저장·completion metadata를 공통 처리한다.
- 9,856줄 파일을 다음 책임으로 분리한다.
  - request planning과 정책
  - dataset retrieval과 filtering
  - evidence/generation pipeline
  - direct handlers
  - observability persistence
  - admin/readiness endpoints
- FastAPI lifespan context로 startup/shutdown을 옮기고 Pydantic v2 model config를 사용한다.
- serving과 scheduler/ingestion worker를 분리하거나 DB 기반 leader lock으로 단일 실행을 보장한다.
- SQLite/ontology schema에 migration version과 backup-before-migrate 절차를 도입한다.

### P2

- API 프로세스를 다중 worker로 늘리기 전 모델 메모리 중복, SQLite write contention, Chroma client thread/process safety를 부하 테스트한다.
- long-running rebuild와 평가를 작업 큐로 분리하고 상태 조회·취소·재시도 계약을 제공한다.

## 성능 개선 작업

- 전체 요청 deadline 안에 analysis, retrieval, selector, generation, grounding별 하위 timeout을 둔다.
- 요청 취소를 threadpool 검색과 모든 provider 호출까지 전파한다.
- startup에서 6개 데이터셋과 embedder를 순차 warmup하는 비용을 측정하고, 안전한 항목은 제한 병렬화한다.
- immutable dataset snapshot을 원자 교체해 검색 중 lock 시간을 줄인다.
- `rag_service.py` 내부의 반복 DataFrame 변환과 JSON 직렬화를 profile하고 corpus revision 단위 캐시로 옮긴다.
- 메모리 예산을 정한다. 현재 Chroma 약 1.1GB, 모델 아티팩트 약 2.6GB이므로 container limit과 startup headroom을 함께 잡아야 한다.

## 운영 SLO 제안

| 항목 | 목표 |
|---|---:|
| `/live` 성공률 | 99.9% 이상 |
| `/ready` 전환 시간 | 배포 후 5분 이내 |
| 일반 질의 전체 p95 | 8초 이하 |
| 직접 응답 p95 | 800ms 이하 |
| 오류/취소 후 미완성 답변 저장 | 0건 |
| stale 데이터 기능 노출 | 0건 |
| 중복 scheduler 실행 | 0건 |

## 완료 조건

- 스트리밍과 비스트리밍이 동일 입력에서 같은 route, sources, fallback, grounding 상태를 반환한다.
- 하나의 provider 장애가 관련 단계의 명시적 degraded 상태로만 전파되고 전체 API를 불필요하게 중단하지 않는다.
- scheduler를 2개 replica 환경에서 실행해도 각 job이 정확히 한 번만 시작된다.
- startup, reload, rollback, graceful shutdown 시나리오가 자동 통합 테스트로 검증된다.
- DB schema version과 artifact revision을 `/ready` 및 배포 manifest에서 확인할 수 있다.
