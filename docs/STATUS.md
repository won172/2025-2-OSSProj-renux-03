# Dongttok Current Status

Last Updated: 2026-09-28

이 문서는 현재 작업과 다음 실행 순서의 요약이다. 코드·로컬 검증 기준이며,
운영 서버의 실시간 상태나 배포 승인 여부를 대신하지 않는다.
설정 상태는 코드 기본값을 기준으로 기록한다.

## Current Goal

동똑이 RAG 검색 품질과 서비스 안정성을 개선한다.
질문에 맞게 구조화 SQL 관계 조회, 키워드 검색, 벡터 검색을 선택하고,
동국대학교 공식 출처와 정본 데이터에 근거한 답변을 유지한다.

## System Status

| Component | Status | Notes |
|---|---|---|
| Frontend | Stable / Revalidation Needed | React/Vite/TypeScript. 기존 테스트·lint·build 통과 기록 있음. 9월 27일 재검증하지 않음 |
| Main Backend | Stable / Revalidation Needed | ASP.NET Core. 기존 계약 테스트 통과 기록 있음. 9월 27일 재검증하지 않음 |
| RAG Server | Active Development | 질문별 검색 경로 개선 중. 스트리밍·일반 응답의 검색 계획 공통화 완료 |
| Crawler | Active / Operational Verification Needed | 자동 수집, 실패 게시판 재시도, freshness gate, 교직원 변경 승인 구현. 현재 원천 수집·스케줄러 동작은 별도 확인 필요 |
| SourceDocument | Active / Consistency Gate Blocked | RAG 정본. 로컬 strict lineage 검사에서 5개 데이터셋 통과, 공지는 Chroma 검사 오류로 검증 불가 |
| Dense Retrieval | Implemented / Notices Verification Blocked | Chroma 기반 검색과 sparse-only degraded 경로 구현. 공지 dense 정합성은 현재 확인 불가 |
| Lexical Retrieval | Active | FTS5/BM25. SQL로 좁힌 규정 문서 내부 청크 재정렬에도 사용 |
| Reranking | Rule-based Active / Model Disabled | 최신성·필터·후보 융합 적용. Cross-encoder는 `RERANKER_ENABLED=0`이 기본값 |
| Ontology Retrieval | Scoped SQL Active / Broad Expansion Experimental | `RAG_STRUCTURED_RETRIEVAL_ENABLED=1`. 명시적 연락처·과목·학번 질문만 관계 근거 우선 조회. Shadow·광범위한 후보 보강은 기본 OFF |
| Grounding | Implemented / Quality Evaluation Pending | 공식 출처 기반 생성·근거성 검사 구현. `verification_status`(passed/failed/unavailable/not_required)를 RAG completion에 노출(PR #15). C#·프런트 전달은 미적용. 전체 실제 후보 답변의 품질 통과를 의미하지 않음 |
| iOS | In Progress | 인증 쿠키 전송 등 수정 반영. 실제 기기·인증·응답 전달 추가 검증 필요 |

## Current Priorities

### P0

- 로컬 공지 Chroma의 compactor 오류를 진단하고 strict lineage 검사를 다시 통과시킨다.
  운영 서버에도 같은 문제가 있는지는 아직 확인하지 않았다. 기존 DB/컬렉션을 삭제하지 않고
  대상 포인터·아티팩트를 확인한 뒤 복구 방법을 결정한다.
- 실제 후보 endpoint의 전체 골든 평가와 결과·manifest를 확보한다.
  현재 후보 URL이 없으면 CI가 평가를 skip하므로, CI 성공만으로 출시 가능하다고 판단하지 않는다.

### P1

- 이미 구현된 regression 평가 도구를 같은 질문·기준일·corpus/model revision으로 반복 실행한다.
- 실제 질문과 사람 판정 qrels를 확대한다. 관계형 질문 목표는 200건이며 교과목 실사용 표본을 보강한다.
- Retrieval 품질과 cold/warm 지연, p50/p95, fallback을 함께 측정한다.
- SourceDocument/Parquet/Chroma 및 lexical revision 정합성을 변경·배포 전 검증한다.
- 공통 검색 계획 이후 남아 있는 근거 선택·생성·fallback 실행 코어를 통합한다.
- 단순 질문의 근거 선택 LLM 우회와 제한 동시성 검색을 회귀 평가로 검증한다.

### P2

- Ontology 후보 보강·cross-encoder 활성화는 사람 판정 및 실제 후보 품질·지연 gate 통과 후 평가한다.
- Parent document context와 청크 표현 품질을 개선한다.
- iOS 실제 기기, 로그인/세션, 스트리밍·출처 전달을 검증한다.

### P3

- UI/UX 개선.

## Task Board

멀티 agent 작업의 handoff 표. **Orchestrator가 일괄 갱신 PR(`docs/status-*`)로만 갱신한다.**
task PR과 구현·QA agent는 이 문서를 수정하지 않고, 결과는 Completion Report로 전달한다. 절차는 [AGENTS.md](../AGENTS.md),
[CLAUDE.md](../CLAUDE.md)를 따른다.

Status 값:

| Status | 의미 |
|---|---|
| `Planned` | 정의됨. branch/worktree 아직 없음 |
| `In Progress` | branch·worktree 생성, agent 할당, 구현 중 |
| `Review` | Completion Report 제출됨. QA/Review 및 Orchestrator 재검증 중 |
| `Blocked` | 외부 결정·데이터·승인 대기. Known Issues에 이유 기록 |
| `Completed` | human 승인 후 merge 확인, worktree 제거 완료 |

| Task | Priority | Owner Role | Branch | Worktree | Status | Tests | Known Issues | Next Action |
|---|---|---|---|---|---|---|---|---|
| 프런트 fallback 사유 라벨 전체 매핑·추천 질문 요청 취소 (audit 08) | P1 | Client | `fix/client-fallback-labels` | `client-fallback-labels` | Review | 2026-09-28 로컬: 59/59·lint·build·tsc. 구현 codex, QA codex2 2회 → APPROVE. main 병합 후 재실행 동일 | iOS 네이티브 요청은 물리 취소 불가(결과만 무시). 실기기 미검증 | PR #21 merge 승인 대기 |
| 메인 서버 RAG 중계 timeout 단계 분리 (audit 08 P1) | P1 | RAG/Backend (ASP.NET Core) | `fix/rag-relay-timeouts` | `rag-relay-timeouts` | Review | 2026-09-28 로컬: Release build 오류 0, 계약 테스트 통과(실제 relay 경로·SSE frame 파싱). QA codex2 7회 | 빈 upstream 뒤 fallback 쓰기는 total deadline 밖(저장 안 됨). 실 Kestrel·DB E2E 미실행. diff 큼(약 +390/-260) | PR #22 merge 승인 대기(리뷰 유의) |
| PWA precache 축소·bundle budget 검사 (audit 08 성능) | P2 | Client | `fix/frontend-precache-budget` | `frontend-precache-budget` | Review | 2026-09-28 로컬: 50/50·lint·build·budget(precache 19개 1,184,847B, main JS 237,660B). QA codex2 APPROVE | main JS budget 여유 약 1%. 일부 공유 chunk는 오프라인 시 runtime cache 후 사용. 실제 오프라인 미검증 | PR #23 merge 승인 대기 |
| `_academic_period`가 학수번호를 연도로 읽는 버그 수정 | P1 | RAG/Backend | `fix/academic-period-course-code` | `academic-period-course-code` | Review | 2026-09-28 로컬: 전체 RAG 1,223 passed/1 skipped(일반·빈 DB). 구현 codex 4회, QA codex2 3회. 핵심 사례 Orchestrator 직접 확인 | 재색인 후 반영. `2024.12.2` 같은 날짜에서 학기 추출 제거(의도된 차이). 실제 qrels 영향 미확인 | PR #26 merge 승인 대기 |
| fallback 사유 세분화(미발표·stale·clarify·범위 밖·selector 거절) (audit 03 P1) | P1 | RAG/Backend | `feat/fallback-reasons` | `fallback-reasons` | Review | 2026-09-28 로컬: 관련 42개, 전체 RAG 1,184 passed/1 skipped(일반·빈 DB). 구현 codex, QA codex2 APPROVE | 새 reason 5개는 `fallback_triggered=true` → 메인 서버 IsFallback·fallback 비율 지표 상승. 프런트 전용 라벨은 #21 merge 후 후속 | PR #25 merge 승인 대기. 다음: 공통 QueryPlan(#25 merge 후) |
| 공지 Chroma compactor 오류 진단·strict lineage 재통과 (P0) | P0 | RAG/Backend | - | - | Blocked | - | 실제 DB·artifacts 사본 필요. 에이전트의 사본 생성은 개인정보 처리로 권한 차단 | human이 읽기 전용 사본 경로 제공 또는 권한 허용 |
| 실제 후보 골든 평가 190문항 (P0) | P0 | QA | - | - | Blocked | release gate는 fail-closed로 전환(PR #14) | 후보 endpoint·모델 비용 승인 필요 | human이 endpoint 제공 |
| Orchestrator·Codex 실행 설정 후보 적용 | P1 | Orchestrator | `chore/agent-orchestration` | `agent-orchestration` | Blocked | runner 단위 테스트 22/22(2026-09-27 재실행) | staged 상태. 에이전트 권한 설정 commit이 auto mode에서 차단됨. base `cffe3e3`로 오래됨 | human이 commit·PR 여부 결정 |

Worktree 경로는 `../dongttok-worktrees/<slug>` 기준으로 적는다.
완료된 행은 다음 갱신 때 Recently Completed로 옮긴다.

## Git State

2026-09-28 기준 확인값. 원격 상태는 `git fetch` 시점에 따라 달라진다.

- Remote: `origin` = `github.com/won172/2025-2-OSSProj-renux-03` (fork, upstream `CSID-DGU/2025-2-OSSProj-renux-03`).
- `origin/main` = `5ce5e85` (PR #13~#20 merge). branch protection 없음(human 설정 필요).
- 새 task의 base branch는 `origin/main`이다. PR #13 이후 task PR은 STATUS.md를 수정하지 않는다.
- merge된 task의 worktree·local branch는 정리했다. remote branch(`chore/golden-release-gate`,
  `fix/grounding-verification-status`, `chore/build-lineage-gate`, `fix/chunk-representation`,
  `feat/official-rules-coverage`, `feat/notice-deletion-detection`, `docs/codex-executors`,
  `docs/status-batch-update` 등) 삭제 여부는 human 결정 대기.
- 규칙 도입 전 branch(`codex/*`, `auto/*`, `redesign/*`, `v2-agentic-graph-rag`,
  `docs/*-20260927`)는 정리하지 않고 유지한다.
- 구현은 Codex 우선(`codex` Pro Lite 구현, `codex2` Plus 읽기 전용 QA, PR #20). 계약·보고는 git 밖
  `../dongttok-worktrees/.codex-runs/`에 둔다.

## In Progress

### RAG Evaluation

Owner: RAG Agent

Goal:
기존 질문 세트와 실제 질문을 이용해 retrieval 변경 전후 품질·지연을 비교하고,
공식 출처·grounding·답변 계약의 회귀를 확인한다.

Status:
부분 구축 완료 / 사람 판정 및 실제 후보 평가 대기. 단순 Planning 단계는 아니다.

- 골든 매트릭스: 12개 학생 도메인, 190개 질문. 9월 27일 구조 검증 통과.
- 기존 retrieval qrels: 164개 질문, 11,731행. `human` 라벨은 13행이며 대부분 자동 라벨이다.
  사람 판정 질문 164개가 확보됐다는 의미가 아니다.
- 구조형 SQL 우선 검색 비교(9월 23일): 45건, Recall@3 0.896 → 0.938,
  nDCG@shortlist 0.938 → 0.991. 정본 기반 fixture 결과이며 실제 답변 품질 증거는 아니다.
- 실로그 비교(9월 23일): 적격 질문 28건, SQL 적용 18건, 후보 변화 17건.
  변경 문서 검토 목록 82행은 사람 판정 대기다. 교과목 실로그 표본은 0건이다.
- Cold 예열을 분리한 구조형 비교의 warm p50/p95: hybrid 후보 약 119/127ms,
  SQL 관계 조회+후보 약 75/130ms. p95 개선은 입증되지 않았다.
- 후보 HTTP 실행·결과 평가·revision fingerprint·전후 비교 도구는 구현돼 있다.
  `tests/replay/latest/results.jsonl`과 `manifest.json`은 현재 체크아웃에 없다.

## Known Issues

### ISSUE-001 — 실제 질문의 정량 회귀 판정 근거 부족

Impact: High

평가 도구와 질문 세트는 있으나 대부분의 retrieval relevance 라벨이 자동 생성이며,
SQL 경로에서 바뀐 실로그 후보에는 사람 판정이 없다. 구조형 fixture 개선만으로
전체 질문의 검색·답변 품질 개선을 판단할 수 없다.

Next Action:
변경 후보 17건을 먼저 판정하고, 최근 질문·교과목 질문과 expected source를 보강한다.
자동 라벨과 사람 라벨의 평가 결과를 분리한다.

### ISSUE-002 — 로컬 공지 dense lineage 검사 실패

Impact: High / P0

9월 27일 `report_canonical_lineage.py --mode strict`는 공지에서
`InternalError: Failed to apply logs to the metadata segment` compactor 오류로 실패했다.
rules, schedule, courses, staff, meals는 통과했다.
공지의 mismatch 여부는 검사할 수 없었으며, 운영 서버 장애를 확인한 결과는 아니다.

Next Action:
로컬 활성 컬렉션·포인터·아티팩트와 검사 환경을 확인하고, 비파괴 복구 계획을 세운다.
복구 후 전체 strict gate를 재실행한다.

### ISSUE-003 — 실제 후보 릴리스 검증 증거 미확보

Impact: High

현재 골든 후보 CI는 `RAG_GOLDEN_BASE_URL` 미설정 시 평가를 건너뛴다.
전체 실제 HTTP 결과와 provenance manifest가 없는 상태에서는 테스트 통과를
실제 RAG 품질 또는 배포 승인으로 해석할 수 없다.

Next Action:
후보 환경을 정하고 현재 190개 질문 전체를 실행해 공식 출처·답변·grounding·후속 질문을
검증한다. 합성 fixture 및 일부 문항 실행은 출시 증거에서 제외한다.

### ISSUE-004 — 실행 코어 중복 및 tail latency 검증 미완료

Impact: Medium

스트리밍·일반 응답의 검색 계획은 공통화했지만 근거 선택·생성·fallback은 중복이 남아 있다.
로컬 후보 검색의 p50 개선을 전체 endpoint 또는 동시 요청의 p95 개선으로 볼 수 없다.

Next Action:
응답 계약을 유지하며 실행 코어를 단계적으로 통합하고 실제 endpoint·동시 요청을 측정한다.

## Recently Completed

- Dense + lexical 하이브리드 검색과 결정적 일정·급식 응답 구현.
- Grounding 검사와 공식 출처·source attribution 계약 구현.
- SourceDocument 정본, corpus revision, 파생물 계보 및 strict lineage gate 구현.
- 수집 freshness gate, 공지 실패 게시판 재시도, 교직원 변경 승인 경로 구현.
- Revision-bound ontology build DAG와 원천 구조 fingerprint 적용.
- 명시적 연락처·과목·학번 질문의 SQL 관계 근거 우선 검색 적용.
- 스트리밍·일반 API 공통 검색 계획 및 일부 단순 질문의 분석 LLM 우회 적용.
- 구조형/실로그 SQL 우선 검색 비교와 데이터셋별 cold/warm 측정 분리.
- 9월 27일 전체 RAG pytest: 977 passed, 9 deprecation warnings.
  로컬 HTTP 테스트를 위한 소켓 권한을 허용한 재실행 결과다.
- 9월 27일 CI가 프런트 `npm test`와 백엔드 계약 테스트를 실제 실행 (PR #8, pipeline-audit P0).
- 9월 27일 멀티 agent 개발 규칙(AGENTS.md, CLAUDE.md, Task Board) 도입 (PR #7).
- 9월 27일 RAG 테스트 순서 독립성 검증 및 실제 Chroma·유지보수 잠금 격리 (PR #10).
- 9월 27일 CI에 RAG 무작위 순서 pytest 단계 추가, `requirements-dev.txt` 도입 (PR #11).
- 9월 27일 질의분석 체인 주입 지점 통일·싱글턴 초기화·동시 첫 생성 잠금 (PR #12).
- 9월 27일 STATUS.md는 Orchestrator 일괄 PR로만 갱신 (PR #13).
- 9월 27일 골든 후보 평가 release gate fail-closed: 수동 릴리스 실행에서 URL 없으면 실패, 불완전·부분 manifest 거부 (PR #14). 실제 평가는 미실행.
- 9월 27일 grounding `verification_status` 4상태, 미검사를 통과로 표시하지 않음, stale 직접응답 grounded 제거, cache는 passed만 (PR #15).
- 9월 27일 index build·rebuild 스크립트 strict lineage gate (PR #16). 실제 데이터 실행 미검증.
- 9월 27일 청크 표현: courses 화이트리스트·raw_text 투영, rules 조문 단위 분할 (PR #17). **재색인 전에는 효과 없음.**
- 9월 27일 공식 규정 전체 분류 수집(77개), fail-safe·보고 (PR #18). 실제 전체 sync·재색인 미실행.
- 9월 27일 삭제 공지 탐지 opt-in(기본 off, 2회 probe 확인·cap) (PR #19). 운영 dry_run 미실행.
- 9월 28일 Codex 우선 위임 규칙 (PR #20).

## Agent Tasks

아래는 역할별 작업 계획이며 실제 병렬 에이전트 실행 상태를 뜻하지 않는다.
실제로 할당·실행 중인 task는 위 Task Board에만 기록한다.

### RAG Agent

Current:
- 검색 전략·평가 도구 구현 완료 부분을 정리하고 회귀 검증 근거를 확보 중.
- 로컬 공지 lineage 검사 실패를 확인한 상태. 복구는 미실행.

Next:
- 공지 P0 진단·복구 계획 수립 및 strict gate 재검증.
- 같은 revision의 baseline/candidate 평가와 남은 실행 코어 공통화.

### Client Agent

Current:
- 이 RAG 작업 범위에서는 idle.

Next:
- iOS 실제 기기·인증·스트리밍·출처 계약 검증.

### QA Agent

Current:
- 기존 골든 질문 및 자동 qrels를 바탕으로 사람 판정 데이터 보강 필요.

Next:
- 실로그 변경 후보 판정, expected source 검토, 최근·교과목 질문 확충.
- 후보 endpoint 전체 실행과 결과·manifest 검증.

## Human Decisions Required

- 실로그 변경 후보의 relevance·관계 정확성 사람 판정 및 검토 담당자 확정.
- 실제 평가에 사용할 후보 endpoint/검증 환경과 모델 호출 비용 승인.
- 품질·정합성 gate 통과 후 운영 배포 여부 승인. 현재 배포를 승인한 상태는 아니다.
- GitHub `main` branch protection(직접 push·force push 차단, PR·CI 필수) 적용 여부.
  원격 저장소 설정 변경이므로 agent가 수행하지 않는다.
- 골든 평가 threshold(현재 0.75/0.80/0.70/0.80, 감사 권고 0.85) 상향 여부.
- PR #18 공식 규정 `--dry-run` 결과(폐지 후보 약 24건, 이름 변경 약 22건) 검토 후 실제 sync·rules 재색인 여부.
- PR #19 삭제 공지 탐지 운영 `dry_run` 관찰 후 `enforce` 전환 여부.
- PR #17·#18 반영을 위한 재색인과 전후 비교에 쓸 데이터 사본 제공(에이전트 사본 생성은 권한 차단).
- merge된 task의 remote branch 삭제 여부, `chore/agent-orchestration` 후보 처리.

## Next Milestone

RAG Evaluation v1 — 사람 판정과 실제 후보 비교가 가능한 회귀 기준선.

Definition of Done:

- [x] 최소 100개 질문 확보: 현재 골든 매트릭스 190개, 구조 검증 통과.
- [ ] 사람 판정 expected source/document_key와 relevance 정의.
  관계형 평가 확대 목표는 200개 질문이며 자동 라벨은 사람 판정으로 세지 않는다.
- [x] Retrieval 평가 도구 구현: 구조형·실로그 후보 및 지연 비교 가능.
- [ ] 현재 데이터의 strict lineage 전체 통과 및 동일 revision 기준선 확보.
- [ ] 실제 후보 190개 질문 전체의 답변·출처·grounding 평가 완료.
  기존 계약 지표와 사람 의미 판정을 구분한다.
- [ ] 동일 질문·기준일·corpus/model revision에서 변경 전/후 비교 보고서 확보.
- [ ] 결과·manifest·fingerprint를 검증하고 회귀 발견 시 승격을 중단하는 절차 확정.

## References

- [전체 파이프라인 계획 및 실행 순서](../src/RAG/docs/pipeline-audit/README.md)
- [검색·후보 융합](../src/RAG/docs/pipeline-audit/02-indexing-retrieval.md)
- [온톨로지·질문별 검색 경로](../src/RAG/docs/pipeline-audit/05-ontology-relational-retrieval.md)
- [API 실행 코어 통합](../src/RAG/docs/pipeline-audit/06-api-runtime-operations.md)
- [검색 평가와 관측](../src/RAG/docs/pipeline-audit/07-evaluation-observability.md)
- [정본 계보 검사 runbook](../src/RAG/docs/canonical-lineage-runbook.md)
- [골든 매트릭스](../src/RAG/tests/golden_matrix.csv)
- [실제 후보 replay 증거](../src/RAG/tests/replay/README.md)

기존 pipeline-audit 문서는 최초 감사 스냅샷과 날짜별 진행 기록을 포함한다.
현재 요약은 이 문서를 먼저 보되, 수치의 측정일과 로컬/운영 검증 범위를 함께 확인한다.
