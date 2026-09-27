# 02. 인덱싱·검색·후보 융합

## 목표와 필요성

학내 정보는 정확한 명칭·전화번호·과목코드와 자연어 표현이 섞여 있어 sparse와 dense 검색을 함께 쓰는 것이 적합하다. 따라서 하이브리드 검색은 **현재 프로젝트에 필요**하다. 다만 인덱스 정합성이 깨진 상태에서는 가중치 튜닝보다 재구축이 먼저다.

주요 구현은 [hybrid.py](../../src/search/hybrid.py), [chroma_client.py](../../src/vectorstore/chroma_client.py), [rag_service.py](../../api/rag_service.py)에 있다.

## 현재 상태

- 37,696개 청크를 데이터셋별 Parquet/FTS5/Chroma에서 조회한다.
- dense 검색 실패 시 sparse-only로 내려가는 degraded 경로가 있다.
- 날짜, 마감, 캠퍼스, 사용자 audience, 학과, 입학연도 필터가 검색 전후에 적용된다.
- 최신 공지, 진행 중 공지, 날짜가 정해진 일정은 일반 의미 검색보다 결정적 필터/정렬을 우선한다.
- 단일 데이터셋의 명시적 관계 질문은 SQLite 관계 근거를 먼저 조회한다. 교직원·교과목은 해당 문서 후보를, 학번별 기준은 관계로 좁힌 규정 문서 안의 키워드 상위 청크를 사용한다. 근거가 없거나 오래됐으면 하이브리드 검색으로 복귀한다.
- 데이터셋별 후보를 균형화하고 RRF로 융합하며, cross-encoder reranker는 기본 OFF다.
- 규정 SQL 경로의 문서 내부 청크 재정렬은 FTS5/BM25를 사용한다. 해당 문서의 청크 rowid만 점수화하도록 제한해 광범위한 FTS5 OR 질의가 전체 규정 코퍼스를 점수화하지 않게 했다. 비교 평가에서 보인 약 1.6초 첫 요청 이상치는 이 점수 계산이 아니라 데이터셋의 cold 로딩(아티팩트/모델 포함)이었다. 로딩 비용은 warm 검색 지연과 따로 측정한다.
- 데이터셋과 query expansion 검색이 `for` 루프에서 순차 실행된다. 한 요청에서 여러 데이터셋을 타면 지연이 누적된다.
- lexical DB는 약 21MB, Chroma는 약 1.1GB다. 모델 디렉터리는 약 2.6GB이고 543MB quantized ONNX 파일을 포함하지만 현재 런타임 코드에서 ONNX 추론 경로를 찾지 못했다.

최근 실트래픽 표본에서 검색·융합의 조건부 평균은 약 500ms다. 이 값은 평균일 뿐이며 p95와 데이터셋별 분포가 없어 병목과 tail latency를 판단하기 어렵다.

## 기능 개선 작업

### P0

- [01 문서](01-ingestion-canonical.md)의 공지/과목 정합성 문제를 해결하기 전 검색 파라미터를 바꾸지 않는다.
- 각 검색 결과에 `corpus_revision`, `document_key`, `chunk_id`, sparse/dense 원점수, fusion rank를 항상 남겨 재현 가능하게 한다.
- dense failure를 단순 fallback으로 숨기지 말고 completion metadata와 운영 지표에 `retrieval_mode=sparse_degraded`를 전달한다.

### P1

- 공지, 직원, 규정, 과목, 일정, 급식별 qrels를 분리하고 데이터셋별 top-k, RRF 상수, 필터 순서를 튜닝한다.
- 공지 검색 실패를 `게시일`, `마감일`, `현재 유효`, `게시판`, `학과 공개범위` 오류로 분해해 개선한다.
- 직원 검색에 부서/학과 별칭, 직책, 업무명, 전화번호 형식 정규화를 추가한다. 최근 표본에서 staff route fallback은 18건 중 7건으로 높다.
- active notice는 후보를 100개까지 넓히는 현재 방식 대신 deadline/status 인덱스를 두어 먼저 eligible set을 줄인다.
- query expansion 결과가 같은 문서를 반복 가점하지 않도록 현재 온톨로지와 동일한 “관찰 1회” 원칙을 일반 후보 융합에도 적용한다.
- 검색 결과가 단일 고신뢰 문서인지, 충돌하는 복수 문서인지 표시해 다음 단계의 LLM 사용 여부를 결정한다.
- SQL 우선/기존 hybrid를 같은 production shortlist에서 재생하는 구조형·실로그 평가를 유지한다. 실로그 후보 변화는 사람 판정 전까지 검색 품질 개선으로 간주하지 않는다.

### P2

- reranker는 사람이 판정한 qrels에서 nDCG/Recall 개선이 통계적으로 확인되고 p95 예산을 만족할 때만 켠다.
- sparse backend를 FTS5로 단일화할지 pickle BM25 호환 경로를 유지할지 결정하고, 사용하지 않는 아티팩트는 빌드/배포에서 제외한다.
- ONNX 추론을 실제로 사용할 계획이 없다면 2.6GB 모델 번들에서 제거한다. 사용할 경우 SentenceTransformer 경로와 같은 임베딩 결과/정규화 계약을 검증한 뒤 선택적으로 도입한다.

## 성능 개선 작업

1. **제한된 데이터셋 병렬 검색**: `_ensure_dataset`과 Chroma client의 thread safety를 테스트한 뒤 2~3개 동시 작업으로 검색한다. 결과 합치기 순서는 고정해 결정성을 유지한다.
2. **query expansion 예산**: 단일 검색 모드를 기본으로 유지하고, 확장 질의는 첫 검색의 신뢰도가 낮을 때만 추가한다.
3. **필터 인덱스 사전 계산**: audience, department, year, date mask를 매 요청 DataFrame 연산으로 만들지 않고 corpus revision 단위로 캐시한다.
4. **후보 수 단계화**: 데이터셋 크기와 질의 유형에 따라 retrieval top-k → fusion top-k → context top-k 예산을 명시한다.
5. **임베딩 캐시 계측**: 현재 프로세스 내 query embedding cache의 hit ratio를 기록하고, 다중 프로세스에서는 작은 공유 캐시의 실익을 측정한다.
6. **시맨틱 캐시 안전화**: `corpus_revision + 모델 revision + 응답 계약 version`을 키에 넣고, 날짜·마감·대화 의존 질의는 제외한다.

## 권장 성능 예산

| 단계 | 1차 목표 | 비고 |
|---|---:|---|
| 단일 데이터셋 검색 p95 | 350ms 이하 | warm 상태, 로컬 dense 포함 |
| 다중 데이터셋 검색·융합 p95 | 800ms 이하 | 최대 3개 route 기준 |
| degraded 전환 탐지 | 1분 이내 | dense 오류율과 데이터셋 식별 |
| Recall@10 | 현재 기준 대비 비열화 없음 | 전체와 데이터셋별 모두 확인 |
| nDCG@10 | 변경 전보다 개선 | reranker/가중치 변경 시 필수 |

## 완료 조건

- 동일 revision과 질의로 top-k 결과를 재현할 수 있다.
- dense 손상/불가 상태가 사용자 응답 메타데이터와 운영 대시보드에 드러난다.
- 병렬화 후 Recall@10 비열화 없이 검색 p95가 줄어든다.
- 공지와 직원 route fallback의 원인별 비율이 측정되고, 검증 세트에서 현재 대비 절대 5%p 이상 감소한다.
- 모델/인덱스 아티팩트 중 런타임 미사용 항목이 manifest에서 명시되거나 배포물에서 제거된다.
