# 01. 수집·정본·데이터 신선도

## 목표와 필요성

이 계층은 모든 검색과 답변의 사실 기반이다. 현재 프로젝트에 **반드시 필요**하며, 어떤 모델 개선보다 우선한다. 정본과 파생 인덱스가 다르면 검색 품질 측정 자체가 무의미해진다.

주요 구현은 [ingest.py](../../src/pipelines/ingest.py), [database.py](../../src/database.py), [scheduler.py](../../src/services/scheduler.py), [canonical_lineage.py](../../src/services/canonical_lineage.py)에 있다.

## 현재 상태

| 데이터셋 | active | updated | hidden | 기타 | 최근 수집 관찰 | 판단 |
|---|---:|---:|---:|---:|---|---|
| courses | 4,395 | 0 | 12 | 0 | 2026-08-24 | 정본은 있으나 파생물 ID 정합성 실패 |
| meals | 10 | 0 | 235 | 0 | 2026-08-29 | 2026-09-21까지 partial 70회 누적, 0건 수집 반복 |
| notices | 4,616 | 1,425 | 6 | deleted 8 | 2026-08-29 | 일부 게시판 불완전 이력, dense 저장소 오류 |
| rules | 624 | 0 | 105 | parse_failed 1 | 2026-08-24 | 계보 정합성 양호 |
| schedule | 102 | 0 | 102 | 0 | 2026-08-29 | 계보 정합성 양호, active/hidden 의미 점검 필요 |
| staff | 4,303 | 0 | 0 | 0 | 2026-08-09 | 정기 갱신 작업이 없음 |

`report_canonical_lineage.py --mode observe` 결과는 다음과 같다.

- notices: Chroma compactor backfill 오류로 검사 자체가 실패했다.
- courses: 정본 4,395건과 파생 문서 4,203건의 ID 집합이 완전히 달라 mismatch ratio 1.0이었다.
- rules, schedule, staff, meals: 검사 범위에서 정본-파생물 정합성이 맞았다.
- notices 데이터 품질 검사는 6,055건 중 index mismatch 20건(0.33%)으로 임계치 안이지만, dense 계보 오류와는 별개의 검사다.

## 2026-09-21 P0 적용 결과

- Apple Silicon 로컬 작업은 `EMBED_DEVICE=mps`로 실행했다. KURE-v1 임베딩(1024차원)의 MPS 동작과 유한값 출력을 먼저 확인했다.
- courses를 정본에서 다시 투영해 4,395문서/8,726청크로 복구했다.
- notices는 격리된 build collection에서 11,951청크를 생성하고 ID·차원·20개 대표 질의를 검증한 뒤 포인터를 교체했다. 이후 정본에만 있던 실제 신규 문서 20건을 증분 반영해 최종 6,041문서/11,982청크로 맞췄다.
- meals를 실제 원천에서 다시 수집했다. 14일 요청·14일 응답·14일 파싱 성공, fetch 실패 0일, D-Flex 13건을 포함해 정본/검색 인덱스 59건으로 복구했다.
- 최종 `report_canonical_lineage.py --mode strict`는 notices, rules, schedule, courses, staff, meals 전부 통과했다. 모든 source/artifact/Chroma mismatch가 0이다.
- 수집 실행에 `outcome_code`와 JSON diagnostics를 저장하고, 프로세스 재시작 후에도 유지되는 freshness 보고서를 readiness와 관리자 상태에 노출했다.
- 현재 시점 질문의 meals/schedule 직접 응답은 데이터가 stale이거나 연속 실패 경고 상태면 기존 데이터를 답하지 않고 최신 확인 불가로 종료한다.
- 전체 Python 테스트는 913개가 통과했다. 테스트 전후 운영 DB의 최신 ingestion run ID가 같아 단위 테스트의 실행 이력 오염도 차단됐다.

P0 이후 남은 staff 정기 갱신·변경 승인, notices 실패 게시판 단독 재시도,
corpus revision의 전 파생물 전파는 2026-09-22 P1에서 완료했다. 온톨로지
재생성 DAG와 원천 구조 fingerprint도 같은 날 P2에서 완료했다.

## 2026-09-22 P1 적용 결과

- staff를 기본 주 1회(`RAG_STAFF_REFRESH_CRON`, 일요일 04:00) 수집 대상으로
  추가했다. 전체 부서가 성공한 완전한 스냅샷만 현재 명부와 비교하며, 변경분은
  immutable JSON snapshot과 `PendingItem(source_type=staff_refresh)`으로 승인 대기한다.
  일부 부서 실패나 0건 수집은 기존 명부를 건드리지 않는다.
- staff 승인 시 snapshot SHA-256을 다시 검증한 후 정본·청크·dense·lexical을
  갱신한다. 승인 적용은 별도의 성공 ingestion run(`approved_review`)으로 기록되어
  freshness가 실제 적용 시각과 corpus revision을 가리킨다. 반려는 기존 명부를
  보존하며, 검수 화면에서 snapshot payload 직접 수정은 막았다.
- 실제 공개 API 검증에서는 1,096개 부서를 모두 성공적으로 읽어 4,557건을
  반환했다. 기존 4,303건에는 upstream ID가 없고 신규 응답에만 `staff_seq`가 있어
  최초 비교가 전량 교체로 보이던 문제를 발견했으며, 최초 전환은 공개 프로필 키로
  연결하고 이후부터 공통 upstream ID를 쓰도록 보정했다. 최종 diff는 추가 790,
  삭제 536, 연락처 변경 48이었다. 변화량이 커 실제 후보 생성·적용은 하지 않았다.
- schedule snapshot 정책을 명시했다. 수집 응답에 포함된 학년도는 완전히 교체하고,
  응답에 없는 과거 학년도는 보존하며, 학년도 없는 legacy 행은 제거한다. 빈 응답과
  학년도 식별 불가능 응답은 기존 데이터를 보존한 채 실패한다. 중복 제거와
  current-year 이전 문서 hidden 전환을 회귀 테스트로 고정했다.
- notices에서 최초 수집이 부분 성공이면 실패한 게시판만 한 번 재시도한다. 최초 실패,
  재시도 회복, 최종 실패 게시판과 오류 유형을 ingestion diagnostics에 남기며 최종 상태는
  `success`, `partial_boards`, `parse_failure`, `upstream_unreachable`로 구분한다.
- `report_data_quality.py --dataset` 선택지는 실제 지원 범위인 `notices`로 제한해
  다른 데이터셋도 검사된다는 잘못된 운영 신호를 없앴다.
- 모든 청크 projection에 결정적 `corpus_revision`을 발급하고 Parquet, BM25 pickle,
  FTS5 metadata, Chroma metadata, 런타임 cache, ingestion run에 전파한다. 런타임은
  chunk와 lexical revision이 다르면 로드를 거부하고, strict lineage는 Chroma metadata
  불일치도 실패로 처리한다.
- 기존 6개 데이터셋을 임베딩 재계산 없이 백필했다: notices 11,982, rules 8,570,
  schedule 102, courses 8,726, staff 4,303, meals 59청크. 백필 후 strict lineage gate가
  전부 통과했다.
- P1 관련 집중 테스트 47개와 전체 Python 회귀 테스트 939개가 통과했다. 남은 9개
  경고는 Pydantic class config와 FastAPI `on_event` deprecation이다.

## 2026-09-22 P2 적용 결과

- `source_schema_fingerprints`를 추가해 데이터셋·원천명·fingerprint별 구조 JSON,
  최초/최근 관찰 시각, 관찰 횟수, 현재 버전과 마지막 ingestion run을 저장한다.
- HTML은 본문 값이나 행 수 대신 DOM tag path, 속성명, semantic attribute와 표 헤더를
  해시한다. CSV/JSON projection은 컬럼 순서·dtype·값 종류를, XLSX는 별도 라이브러리
  없이 ZIP/XML에서 시트명과 헤더를 읽어 해시한다.
- notices, rules, schedule, courses, staff, meals의 기존 정상 snapshot으로 11개 구조
  기준선을 만들었다. schedule 실제 HTML 137,776바이트도 읽어 DOM 기준선을 저장했고,
  courses는 CSV와 XLSX 4개 시트를 각각 추적한다.
- 성공 적재 후 `canonical_lineage → ontology` 순서로 실행하는 파생 DAG를 추가했다.
  lineage가 실패하면 ontology 단계로 넘어가지 않으며, ontology 검증/게시 실패는 전체
  transaction을 rollback해 직전 그래프를 유지한다. 핵심 corpus는 유지하되 해당
  ingestion run은 `partial_success/derivative_failure`로 관측 가능하게 남는다.
- 온톨로지 build revision은 schema version, courses/notices/rules/schedule/staff의
  corpus revision, 검토 별칭 CSV hash로 결정한다. 동일 revision은 재생성을 생략한다.
  런타임 shadow/candidate 경로도 최신 성공 build와 현재 corpus revision이 다르면
  `skipped_stale_revision`으로 fail-closed한다.
- 실제 build run 9를 발급했다. revision은
  `ontology:632a6c0c0d7d76ecc293d7090bf56c75e6d910f8dd9c859291140902c0c799d7`이며
  15,465문서에서 엔터티 9,235개, 별칭 126개, 관계 9,615개, 근거 35,452개를 게시했다.
- 실제 DAG 재실행에서 strict lineage가 통과하고 ontology는 `already_current`로 생략됐다.
  전체 Python 회귀 테스트는 948개가 통과했으며 운영 DB 최신 ingestion run은 ID 504로
  유지돼 테스트 오염이 없다.

## 문제 진단

1. **정본과 검색 가능 상태가 분리되어 있다.** 데이터 품질 보고서가 통과해도 Chroma가 손상되면 실제 검색은 degraded 또는 readiness 실패가 된다.
2. **과목 문서 식별 규칙이 이행 과정에서 달라졌다.** 행 내용이 있어도 동일 문서를 연결할 수 없어 증분 갱신, 삭제, 평가 provenance가 깨진다.
3. **급식 수집 실패가 장기간 반복된다.** 기존 인덱스를 보존하는 것은 안전하지만, 반복 partial을 정상 운영처럼 두면 오래된 식단을 최신으로 오인할 수 있다.
4. **staff 갱신 주기가 없다.** 담당자와 연락처는 변경될 수 있는데 현재 스케줄러에는 자동 수집 경로가 없다.
5. **정본에서 파생되는 온톨로지 재생성이 수집 완료와 연결되지 않았다.** 정본보다 오래된 그래프가 조용히 남을 수 있다.
6. **품질 보고서 CLI 범위가 오해를 부른다.** `--dataset`은 임의 값을 받지만 source-document linkage는 notices만 지원해 다른 데이터셋에서 예외가 발생한다.

## 기능 개선 작업

### P0

- `document_key` 계약을 데이터셋별로 문서화하고 생성 함수를 한곳으로 모은다. 수집기, 정본 upsert, 청크 생성, 평가가 동일 함수를 호출해야 한다.
- courses를 정본에서 다시 투영해 normalized/chunks/FTS/Chroma를 staged build로 만들고, 계보 통과 후 포인터를 원자적으로 교체한다.
- notices는 현재 build collection의 compactor 오류를 복구한다. 검증된 이전 포인터가 있으면 롤백하고, 없으면 정본 기반 staged rebuild 후 전환한다.
- meals는 `0건`, 원천 구조 변경, 네트워크 실패, 파싱 실패를 서로 다른 상태 코드로 저장한다. 연속 실패 횟수와 마지막 정상 수집 시각을 `/ready` 또는 운영 상태 API에 노출한다.
- 날짜 민감 직접 응답은 데이터셋별 허용 신선도보다 오래되면 답을 생성하지 않고 “최신 데이터 확인 불가”를 반환한다.

### P1

- [x] staff를 주기 수집 대상에 추가하고 연락처 변경 diff를 승인/검토할 수 있게 한다.
- [x] schedule의 active/hidden 전환 규칙을 학기/년도 기준으로 명시하고, 동일 일정의 중복 및 이전 스냅샷 보존 정책을 테스트한다.
- [x] notices 부분 성공 시 실패한 게시판만 재시도하고, 전체 수집 성공과 구분된 outcome/diagnostics를 둔다.
- [x] `report_data_quality.py`의 CLI 선택지를 실제 지원 범위인 `notices`로 제한한다.
- [x] 성공한 수집 run이 새 corpus revision을 발급하고, 모든 파생물과 캐시가 그 revision을 기록하도록 한다.

### P2

- [x] 원천별 HTML/CSV/XLSX 구조 fingerprint를 저장해 조용한 스키마 변경을 조기에 탐지한다.
- [x] 정본 변경 이벤트에서 청크·검색 인덱스·온톨로지 build를 순차 실행하는 명시적 DAG를 도입한다.

## 성능 개선 작업

- 변경된 `document_key`만 재청크·재임베딩하고 삭제 tombstone을 함께 처리한다.
- 원천 fetch와 파싱은 사이트별 제한을 지키는 범위에서 병렬화하되, DB 반영은 작은 batch transaction으로 수행한다.
- 콘텐츠 hash가 같은 문서는 임베딩과 FTS 갱신을 생략한다.
- 대형 Chroma 재구축은 serving 프로세스 밖에서 수행하고 포인터 교체 시간만 maintenance lock을 잡는다.
- 507개 ingestion run과 품질 경고의 보존 기간을 정하고, 오래된 상세 로그는 집계 후 archive한다.

## 완료 조건

- 모든 데이터셋에서 `source_missing_artifact=0`, `artifact_missing_source=0`, chunk/dense count가 일치한다.
- staged build 실패 시 현재 포인터와 서비스 검색 결과가 변하지 않는다.
- meals 연속 2회 0건 또는 freshness 초과 시 경고가 발생하고 사용자 응답은 stale-safe하게 동작한다.
- notices 부분 성공은 실패 게시판, 오류 종류, 재시도 결과를 식별 가능하게 남긴다.
- 수집 성공 후 정본 revision과 모든 파생 아티팩트 revision이 하나의 배포 단위로 일치한다.
- 장애 복구 훈련에서 이전 포인터로 10분 안에 롤백할 수 있다.
