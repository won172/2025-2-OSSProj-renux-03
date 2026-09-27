# 05. 온톨로지·관계 검색

## 필요성 판단

온톨로지는 이 프로젝트 전체의 필수 기반은 아니다. 그러나 “이 과목은 어느 학과가 개설하는가”, “이 규정은 어느 입학연도에 적용되는가”, “이 행사를 어느 조직이 관리하는가”처럼 **명시적 관계를 따라가야 하는 질문에는 유용하다.**

따라서 현재 선택은 다음과 같다.

- 구현은 유지한다.
- 정본에서 파생되는 읽기 전용 projection으로 둔다.
- 광범위한 온톨로지 후보 보강은 기본 OFF와 shadow 운영을 유지한다.
- 단일 데이터셋의 명시적 관계 질문은 revision이 일치하는 SQLite 관계 투영을 먼저 조회한다.
- 별도 Neo4j/RDF 인프라와 전면 LLM 관계 추출은 도입하지 않는다.

주요 구현은 [ontology.py](../../src/services/ontology.py), [ontology_retrieval.py](../../src/services/ontology_retrieval.py), [build_ontology.py](../../scripts/build_ontology.py)에 있다. 설계 상세는 기존 [온톨로지 구현 계획](../ontology-implementation-plan.md)을 함께 참고한다.

## 현재 상태

| 항목 | 현재 값 |
|---|---:|
| 엔터티 | 9,235 |
| 검토된 별칭 | 126 |
| 관계 | 9,615 |
| relation evidence | 35,452 |
| build run | 9 |
| build revision | `ontology:632a6c…c799d7` |
| shadow log | 0 |
| 최대 hop | 2 |

현재 구현의 장점은 명확하다.

- `SourceDocument`가 정본이고 그래프는 재생성 가능한 파생물이다.
- 관계마다 evidence가 있어 원문으로 역추적할 수 있다.
- entity type, predicate, hop, 결과 수에 allowlist와 상한이 있다.
- fuzzy/embedding entity linking 대신 명시적 이름과 검토 별칭을 사용한다.
- 사람 이름은 안전한 query identity에서 제외하고 조직 관계를 통해 접근한다.

하지만 운영 활성화 근거는 부족하다.

- `RAG_ONTOLOGY_SHADOW_ENABLED=0`, `RAG_ONTOLOGY_CANDIDATES_ENABLED=0`은 기본값이다. 별도의 `RAG_STRUCTURED_RETRIEVAL_ENABLED=1`은 학과 연락처, 과목코드·개설 학과, 학번별 졸업·교양 기준의 단일 데이터셋 질문에만 적용된다.
- graph build는 성공한 수집의 파생 DAG에 연결됐고, 현재 build revision은 5개
  데이터셋 corpus revision 및 별칭 파일 hash와 일치한다.
- 후보 보강은 현재 notices, rules, schedule만 허용된다. 그래프는 courses/staff 관계도 표현하지만 실서비스 후보로는 쓰지 않는다.
- synthetic 구조 평가 Recall@10은 0.921에서 1.0으로 올랐지만, production shortlist 40건에서는 0.750에서 0.762로 상승 폭이 작고 2건만 결과가 바뀌었다.
- 실제 로그 799건 중 그래프 후보가 생긴 요청은 62건, 순위가 바뀐 요청은 11건이었으나 사람 relevance label이 없다.

## 질문별 검색 경로 (2026-09-23)

- `staff` 연락처·사무실 질문과 `courses` 과목코드·개설 학과 질문은 SQL 관계 근거가 현재 revision에서 발견되면 그 근거 문서를 우선 사용한다. 없거나 청크/캠퍼스 필터에서 탈락하면 기존 하이브리드 검색으로 복귀한다.
- `rules`의 명시적 학번과 졸업·교양 기준 질문은 `VALID_FOR_ENTRY`와 `GOVERNS` 근거로 적용 문서를 확정하고, 해당 문서 청크를 FTS5/BM25로 재정렬한다. 관련 없는 학번의 규정은 후보에 넣지 않는다.
- 최신 공지·날짜 일정·급식의 기존 조건 조회 경로와 일반 서술형 질문의 FTS5+Chroma 검색은 그대로 사용한다. Cross-encoder 리랭커는 평가와 지연 예산 확인 전까지 기본 OFF다.
- 선택된 경로와 SQL 근거 문서 수를 요청 로그에 남긴다. 온톨로지 projection이 오래됐으면 관계 조회 결과를 쓰지 않는다.
- 명시적 과목코드는 SQLite JSON 필드에서 먼저 찾는다. 과목명·검토 별칭은 성공한 ontology build ID 단위의 경량 이름 인덱스로 찾으며, 새 build가 게시되면 다시 만든다. 로컬 `자료구조` 과목명 질문 6회 측정에서 첫 조회 약 26ms, 이후 중앙값 약 8ms였다(이전 전체 엔터티 조회 약 368ms). 임시/테스트 DB는 캐시하지 않는다.

2026-09-23에 새 SQL 우선 경로를 production shortlist 수준에서 비교했다. 공식 정본에서 만든 구조형 qrels 45건은 현재 결정적 라우터에서 모두 예상 데이터셋으로 들어갔다. 상위 3문서 Recall은 hybrid 0.896 → SQL 우선 0.938, nDCG는 0.938 → 0.991이었다. 데이터셋별 cold 예열을 제외한 로컬 warm 상태에서 그래프+후보 구성 p50/p95는 약 75/130ms, hybrid 후보 구성은 약 119/127ms였다. 이 실행에서는 p95 개선을 입증하지 못했다. 별도 온톨로지 구조 계약 75건도 entity link, relation path, alias 모두 1.0으로 유지됐다. 이는 **구조형 fixture 결과**이지 실제 답변 품질의 증거는 아니다.

실제 로그 중 단일 데이터셋·비합성·현 전략에 해당하는 고유 질문은 28건(교직원 24, 규정 4, 과목 0)이었다. SQL 경로가 18건에 적용됐고 17건의 후보 목록이 바뀌었다. 로컬 warm 상태의 SQL 그래프+후보 구성 p50/p95는 약 67/122ms, hybrid 후보 구성은 약 103/122ms였다. 사람 판정 qrels가 없어 개선/악화 여부는 아직 모른다. 현재 정본을 과거 질문에 재생했으므로 시점이 완전히 동일한 비교도 아니다. 아래 200건 사람 판정 질문과 shadow 관찰 기준은 확대 적용 및 리랭커 활성화 전 검증 항목으로 유지한다.

## 기능 개선 작업

### P0 — 활성화 전 필수

- [x] 정본 `corpus_revision`과 `ontology_build_revision`을 연결하고 불일치 시 후보 주입을 자동 중단한다.
- [x] ontology build를 수집 성공 후 파생 작업으로 실행하되, 검증 실패 시 이전 graph snapshot을 유지한다.
- shadow 로그에 linked entity, traversed relation, 증거 문서, 기존 top-k, 보강 top-k, latency를 저장한다.
- [x] current canonical lineage가 실패하면 ontology candidate 실험도 중지한다. 잘못 연결된 정본 ID 위에 graph 품질을 평가할 수 없다.

### P1 — 실험 품질 확보

- 실제 관계형 질문 200건 이상을 모집해 `needed_document_key`와 relation correctness를 사람이 판정한다.
- false positive를 엔터티 중의성, 오래된 관계, 잘못된 방향, 과도한 hop, evidence 부적합으로 분류한다.
- 별칭 126개를 학과/단과대/조직 중심으로 검토 확대하고 승인자·근거·변경 이력을 남긴다.
- temporal relation에는 유효 시작/종료와 학번·학기 조건을 명시한다.
- 사용자 답변에 graph 추론 자체를 출처처럼 보이지 말고, 관계가 가리킨 공식 문서만 출처로 제시한다.

### P2 — 제한 출시

- shadow 기준을 통과한 rules/schedule부터 트래픽 5%에 후보 슬롯 1개로 활성화한다.
- 데이터셋별 kill switch와 즉시 rollback을 유지한다.
- courses/staff 후보 주입은 별도 qrels와 privacy 검토를 통과한 뒤 확장한다.

## 성능 개선 작업

- [x] 과목명/과목 별칭은 성공한 ontology build ID 단위 경량 이름 인덱스로 캐시한다. 다른 entity type과 relation traversal은 여전히 SQL 조회한다.
- relation traversal은 현재처럼 최대 2-hop, 최대 entity/relation/document 상한을 유지한다.
- 질문당 graph latency와 hit/no-hit를 기록하고, no-hit가 대부분인 route에서는 아예 호출하지 않는다.
- 동일 세션의 동일 normalized entity query는 짧게 캐시하되 revision 변경 시 폐기한다.
- candidate materialization은 document_key별 대표 청크 색인을 미리 만들어 DataFrame scan을 줄인다.

## 승격 기준

| 기준 | 최소 조건 |
|---|---:|
| 사람 판정 관계형 질문 | 200건 이상 |
| Recall@10 | baseline 대비 절대 +3%p 이상 |
| nDCG@10 | baseline 대비 비열화 없음 |
| 잘못된 관계로 인한 오답 | 1% 미만 |
| graph 단계 p95 | 100ms 이하 |
| stale revision 후보 주입 | 0건 |
| shadow 관찰 기간 | 최소 2주 및 500요청 |

## 하지 않을 것

- 온톨로지를 검색 전체의 단일 진실 원천으로 만들지 않는다.
- 근거 없는 LLM 추출 관계를 자동 publish하지 않는다.
- 평가 없이 hop 수나 predicate 범위를 늘리지 않는다.
- 현재 규모에서 운영 부담이 큰 외부 그래프 DB를 먼저 도입하지 않는다.

## 완료 조건

- build 자동화, revision 일치, shadow 데이터, 사람 판정 qrels가 모두 준비된다.
- 제한 출시 전후의 품질·지연·fallback 차이를 같은 질의 집합으로 재현할 수 있다.
- 기능 플래그를 끄면 기존 hybrid 결과와 응답 계약이 완전히 유지된다.
