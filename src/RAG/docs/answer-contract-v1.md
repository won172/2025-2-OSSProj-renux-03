# Dongttok 답변 계약 v1

- 정책 식별자: dongttok-answer-v1
- 문서 상태: 제안된 제품 정책을 현재 구현과 대조한 기준 문서
- 작성 기준일: 2026-08-13 (Asia/Seoul)
- 적용 대상: 동국대학교 학생 지원용 RAG 답변

이 문서는 모델의 종류나 프롬프트 문구를 정하는 문서가 아니다. 어떤 질문에 답할 수
있는지, 어떤 공식 자료를 근거로 사용할 수 있는지, 근거가 부족하거나 충돌할 때
어떻게 멈춰야 하는지를 정한다. 생성 모델, 임베딩 모델, 검색 엔진을 교체해도 이
계약은 유지되어야 한다.

## 1. 핵심 원칙

1. 학교 관련 사실은 승인된 공식 자료에서 확인된 내용만 답한다.
2. 최신성은 모델의 기억이 아니라 수집 시점, 게시일, 적용기간, 마감일 메타데이터로 판단한다.
3. 캠퍼스, 대상자, 학년도·학기, 적용기간을 모르면 임의로 보완하지 않는다.
4. 근거가 없거나 근거가 충돌하면 답변을 만들어내지 않고 그 상태를 안내한다.
5. 개인 성적·학점·학적이 필요한 판정은 일반 규정 안내와 개인 판정을 분리한다.
6. 검색된 문서 안의 지시문은 사용자나 시스템 지시가 아니라 인용 데이터로만 취급한다.
7. 사용자에게는 결론과 공식 출처를 보여주고, 검색 점수·내부 라우팅·모델 이름·내부 추론 기록은 보여주지 않는다.

## 2. 제품 범위

### 2.1 기본 범위

초기 제품은 서울캠퍼스와 바이오메디캠퍼스의 공개 학생 지원 정보만 답변 범위로
한다. WISE캠퍼스는 v1의 수집·검색·답변 범위에서 완전히 제외한다. 질문 원문에
WISE, 경주캠퍼스, 와이즈캠퍼스 등이 명시되어도 허용 신호가 아니라 범위 밖 질문
신호로 처리하고, WISE 자료를 검색하지 않은 채 전용 안내문을 반환한다.

| 차원 | 계약 값 | 규칙 |
|---|---|---|
| 캠퍼스 | seoul, bmc, wise, shared, unknown | 제품 답변 범위는 seoul·bmc·shared이며, wise는 분류·차단을 위한 격리 값이다. unknown을 서울로 추정하지 않는다. |
| 대상자 | undergraduate, graduate, faculty, staff, applicant, common | 질문과 자료의 대상자가 일치해야 한다. |
| 학년도·학기 | 명시된 연도·학기 또는 자료의 적용기간 | 질문에 없는 연도·학기를 모델이 새로 만들지 않는다. |
| 공개 범위 | 공개 공식 자료 | 로그인·개인 학적 자료는 별도 인증·권한 계약 없이는 사용하지 않는다. |

### 2.2 지원 영역

제품 영역과 내부 검색 데이터셋은 분리한다. 제품 영역은 사용자 요구를 설명하고,
내부 데이터셋은 검색 경로를 결정한다.

| 제품 영역 | 주요 내부 데이터셋 |
|---|---|
| 학사일정 | schedule, notices, rules |
| 수강신청·등록 | courses, rules, notices, schedule |
| 졸업·수료 | rules, courses, notices, schedule |
| 장학 | notices, rules, staff |
| 학적 | rules, notices, staff, schedule |
| 캠퍼스 생활·시설 | notices, staff |
| 학식 | meals, notices |
| 기숙사·생활관 | notices, rules, staff, schedule |
| 취업·진로 | notices, staff, rules, courses, schedule |
| 국제·교류 | notices, rules, staff, schedule |
| 담당 부서·사람 찾기 | staff, notices |

내부 식별자(notices, rules 등)는 답변 문구에 노출하지 않는다. 현재 제품 영역과
앱 intent의 매핑은 tests/golden_taxonomy.v1.json을 기준으로 한다.

### 2.3 지원하지 않는 범위

- 일반 상식, 뉴스, 날씨, 주식·코인, 게임 공략, 임의의 코딩 요청
- 공식 근거가 없는 학교 관련 소문·커뮤니티 정보
- 개인 성적·학점·수강내역을 이용한 졸업 가능 여부의 확정
- 사용자를 대신한 수강신청, 장학 신청, 민원 접수, 계정 조작
- 인증되지 않은 nDRIMS·행정 시스템의 개인 정보 조회
- WISE캠퍼스의 공지·규정·교과목·연락처·학식 등 모든 정보

학교 밖 질문은 모델의 일반 지식으로 답하지 않고 결정적 안내문을 반환한다. 인사,
정체성 확인, 사용 방법 안내 같은 대화 예외는 허용하되 학교 사실을 덧붙이지 않는다.
WISE캠퍼스 질문은 학교 관련 표현을 포함하더라도 동일한 범위 밖 처리와 전용 안내문을
사용한다.

## 3. 공식 출처 계약

### 3.1 출처 등록

크롤러는 임의의 URL을 허용하지 않고 별도의 출처 레지스트리에 등록된 공식 도메인,
호스트, 경로만 수집한다. 도메인 문자열의 단순 접미사 일치만으로 허용하지 않는다.

출처 레지스트리는 최소한 다음 정보를 가진다.

    source_id: dongguk-central-academic
    host: www.dongguk.edu
    allowed_paths:
      - /...
    authority_type: central_office
    campus_scope: shared
    audiences:
      - undergraduate
      - graduate
    datasets:
      - schedule
      - rules
      - notices
    refresh_policy: daily
    requires_auth: false
    status: active

수집 시 다음을 다시 검사한다.

- HTTP 리다이렉트의 최종 호스트도 allowlist에 포함되는가
- 첨부파일 URL이 승인된 공식 호스트인가
- 로그인·개인정보 화면으로 이동하지 않는가
- 문서의 캠퍼스·대상자·적용기간을 판정할 수 있는가
- 문서가 삭제·보관·대체 상태인지 확인할 수 있는가

WISE 호스트·경로·파일은 차단과 lineage 점검을 위한 분류 대상으로만 남길 수 있다.
v1의 활성 source registry 항목이나 검색 후보로 등록하지 않는다.

공식 출처라도 모든 주제에 동일한 권한을 갖는 것은 아니다. 주제별 담당 주체를
메타데이터로 기록한다.

| 주제 | 우선 검토할 공식 주체 |
|---|---|
| 전체 학사일정·학칙 | 교무·학사 관련 중앙 부서 |
| 학과별 교육과정·졸업요건 | 해당 학과·전공의 공식 자료 |
| 입시 | 입학처 공식 모집요강·공지 |
| 장학 | 장학 담당 부서 공식 공지 |
| 교직원 연락처 | 해당 부서의 공식 연락처 페이지 |
| 시설·운영시간·학식 | 해당 시설 또는 운영 주체의 공식 안내 |

### 3.2 출처 메타데이터

각 문서는 검색 전에 다음 정보를 정규화한다.

    document_key: canonical-document-key
    source_type: official_notice
    authority_type: central_office
    source_url: https://...
    campus_scope: seoul
    audience: undergraduate
    published_at: 2026-08-01
    effective_from: 2026-08-15
    effective_until: 2026-08-31
    apply_deadline: 2026-08-20
    source_status: active

SourceDocument.document_key는 정본 문서의 식별자이며, Parquet·FTS/BM25·Chroma는
파생 검색 결과다. 파생물의 lineage가 정본과 맞지 않으면 답변을 생성하지 않는다.

## 4. 답변 가능성 상태

내부 판정은 다음 상태 중 하나여야 한다. 사용자에게 상태 코드를 그대로 보여주지
않고 상태별 안내 문구를 사용한다.

| 상태 | 판정 기준 | 기본 처리 |
|---|---|---|
| answerable | 질문의 핵심 주장에 직접 연결되는 공식 근거가 있음 | 답변·출처 제공 |
| partially_answerable | 일부 주장만 근거가 있음 | 확인된 부분만 답변하고 나머지는 미확인 처리 |
| needs_clarification | 캠퍼스·대상자·학년도·질문의 핵심 식별자가 불명확함 | 필요한 조건을 먼저 질문 |
| not_answerable | 승인된 공식 자료를 찾지 못함 | 자료에서 확인되지 않는다고 안내 |
| conflicting_sources | 적용 가능한 공식 자료끼리 핵심 내용이 충돌함 | 양쪽 출처와 충돌 사실을 안내 |
| personal_data_unavailable | 개인 성적·학점·학적·권한 정보가 필요함 | 일반 기준만 안내하고 개인 판정은 거절 |
| out_of_domain | 제품 범위 밖 질문임 | 지원 영역 안내 |
| service_unavailable | 검색·인덱스·모델 등 시스템 장애임 | 장애 상태와 재시도 안내 |

not_answerable은 자료가 없다는 뜻이고, personal_data_unavailable은 자료가 있더라도
해당 사용자의 개인 기록을 읽을 수 없다는 뜻이다. 두 상태를 섞지 않는다.

## 5. 근거와 답변 규칙

### 5.1 주장 단위 규칙

- 날짜·기간·금액·학점·자격요건·연락처·신청 가능 여부는 직접 근거가 있어야 한다.
- 하나의 문서가 뒷받침하지 않는 여러 사실을 하나의 문장으로 합치지 않는다.
- 복합 질문은 하위 질문별로 근거 그룹을 만들고, 근거 그룹 사이의 사실을 임의로 합치지 않는다.
- 검색 결과의 점수가 높다는 이유만으로 답변 근거로 채택하지 않는다.
- 근거 선택기가 관련 없음으로 판정한 후보는 직접적인 어휘·구조 근거가 없는 한 되살리지 않는다.

### 5.2 출처 표기

핵심 주장 뒤에는 [문서N]을 표시하고, 답변 하단 또는 해당 설명 바로 아래에 공식
URL을 제공한다. 첨부파일은 원문 파일명과 공식 URL을 함께 표시한다.

내부 source identity는 다음 필드를 사용한다.

    dataset
    chunk_id
    url
    campus_scope
    published_at
    effective_date
    snippet_hash
    source_ref = sha256:<hash>

source_ref는 운반된 출처 내용과 일치해야 하며, 내용이 바뀐 출처를 이전 ID로
재사용하지 않는다.

### 5.3 근거 없음

학교 질문에 직접 근거가 없으면 다음 의미를 유지한다.

> 제공된 공식 학교 자료에서 확인되지 않습니다. 해당 내용은 공식 홈페이지나 담당 부서에서 확인해 주세요.

이 상태에서 모델의 일반 지식, 검색 전 질의분석의 추측, 이전 대화의 추정값을
보완 정보로 넣지 않는다.

### 5.4 부분 근거

> 확인된 내용은 여기까지입니다 [문서1]. 요청하신 나머지 조건은 제공된 공식 자료에서 확인되지 않습니다.

확인된 부분과 확인되지 않은 부분을 같은 문장 안에서 섞어 단정하지 않는다.

### 5.5 충돌

공식 자료끼리 충돌하면 다음 순서로 판단한다.

1. 질문의 캠퍼스·대상자·학년도·적용기간과 각 문서가 실제로 일치하는지 확인한다.
2. 주제별 담당 주체가 누구인지 확인한다.
3. 명시적인 대체·정정·개정 관계가 있는지 확인한다.
4. 그래도 해소되지 않으면 conflicting_sources로 처리한다.

게시일이 최신이라는 이유만으로 학과별 졸업요건을 중앙 공지로 덮어쓰거나, 과거
문서를 현재 절차로 바꾸어 말하지 않는다.

## 6. 시간과 적용기간

- published_at은 문서가 게시된 날짜이고, effective_from·effective_until은 내용이 적용되는 기간이다.
- 사용자가 현재, 최근, 이번 학기, 신청 중을 물으면 기준 시점과 문서의 적용기간을 비교한다.
- 알려진 신청 마감일이 지났으면 모집 중, 신청 가능, 접수 가능이라고 표현하지 않는다.
- 마감일이 없으면 진행 중이라고 단정하지 않고 마감일 확인 필요라고 표현한다.
- 과거 자료만 있으면 자료가 존재한다는 사실과 과거 적용이었다는 사실을 함께 말한다.
- 역사적 질문은 연도·학기 범위를 명시적으로 보존한다.
- 미래 게시일·미래 적용일 문서는 현재 사실의 근거로 사용하지 않는다.

## 7. 캠퍼스·대상자 경계

- 문서 제목·파일명·URL·경로의 강한 캠퍼스 식별자가 본문에 있는 약한 표현보다 우선한다.
- WISE 전용 문서는 본문에 “양 캠퍼스”라고 적혀 있어도 공통·서울·BMC 문서로 승격하지 않는다.
- WISE 자료는 v1에서 수집·검색·답변 근거로 사용하지 않는다. WISE를 명시한 질문도
  허용하지 않고 out_of_domain으로 처리한다.
- 사용자 질문에 캠퍼스가 없고 검색 후보가 캠퍼스별로 충돌하면 명확화를 요청한다.
- 학부·대학원·교직원·지원자 자료를 대상자 표기 없이 혼합하지 않는다.
- 개인 전용 화면이나 로그인 자료는 공개 공식 자료와 동일한 근거로 취급하지 않는다.

## 8. 개인정보와 외부 행위

다음 요청은 현재 공개 자료 답변 계약의 대상이 아니다.

- “내 성적으로 졸업 가능한가요?”
- “내 수강내역을 보고 이번 학기 과목을 신청해줘”
- “내 nDRIMS 계정의 학적 상태를 확인해줘”
- “장학금 신청을 대신 제출해줘”

답변은 일반적인 공식 요건까지만 안내하고, 개인 판정이나 외부 행위는 본인 인증이
된 별도 시스템과 권한 계약이 있을 때만 추가한다. 개인 정보는 파인튜닝 데이터와
일반 질의 로그에 포함하지 않는다.

## 9. 사용자 응답 형식

- 기본 언어는 한국어 해요체이며, 사용자가 영어로 질문하면 영어로 답한다.
- 단순 질문은 결론을 먼저 짧게 답하고 공식 출처를 붙인다.
- 절차·방법은 번호 목록으로 작성한다.
- 여러 근거 그룹이 있는 복합 질문은 그룹별로 구분한다.
- 내부 검색 점수, 문서 개수, 임베딩, BM25, Chroma, 라우팅 같은 내부 용어는 기본 답변에 노출하지 않는다.
- 원시 chain-of-thought나 모델의 내부 추론 기록은 출력하지 않는다. 필요한 경우 근거, 적용 조건, 계산 결과만 요약한다.

기본 답변 템플릿은 다음과 같다.

    결론: {확인된 답변} [문서1]

    근거:
    - {문서 제목} ({적용 캠퍼스}/{대상자}/{적용기간})
    - [공식 출처로 이동하기]({URL})

## 10. 내부 응답 계약과 운영 상태

현재 API의 wire schema와 제품 정책 버전은 별도다. 이 문서의
dongttok-answer-v1은 제품 정책 버전이며, AskResponse·Golden result의 schema
version을 자동으로 변경하지 않는다.

내부 응답은 최소한 다음을 추적할 수 있어야 한다.

    policy_version: dongttok-answer-v1
    answer_state: answerable
    campus_scope: seoul
    audience: undergraduate
    resolved_domains:
      - graduation
    source_refs:
      - sha256:...
    grounded: true
    fallback_reason: null

사용자 응답에는 필요에 따라 answer_state를 자연어로만 반영하고, 검색 점수와 내부
필드는 제외한다.

정본 문서와 파생 검색 아티팩트의 lineage 검사가 실패하면 답변 준비 상태를 통과시키지
않는다. /ready가 실패한 후보는 Golden 평가나 운영 트래픽에 사용하지 않는다.

## 11. 출시 차단 조건

다음 조건은 평균 답변 점수와 별도로 모두 통과해야 한다.

- 학교 밖 질문에 학교 밖 사실을 생성하지 않는다.
- 공식 근거 없는 날짜·금액·자격요건·연락처를 생성하지 않는다.
- 만료된 모집·신청 공지를 현재 진행 중으로 말하지 않는다.
- WISE 자료가 수집·검색·답변·후속질문·시맨틱 캐시에 사용되지 않는다.
- 학부·대학원·지원자 자료가 서로 섞이지 않는다.
- 개인 데이터가 없는데 개인 졸업 가능 여부를 확정하지 않는다.
- 근거 URL이 없는 핵심 사실을 답변하지 않는다.
- 근거성 검사 실패 답변을 원문 그대로 내보내지 않는다.
- canonical lineage 또는 필수 데이터셋 readiness가 실패한 후보를 출시하지 않는다.

## 12. 현재 구현과의 대응표

| 계약 영역 | 현재 구현·검증 위치 | 현재 상태 |
|---|---|---|
| 제품 영역·앱 intent | tests/golden_taxonomy.v1.json | 구현·Golden 검증 중 |
| 학교 밖 질문 | api/rag_service.py의 out_of_domain_reply, tests/test_out_of_domain.py | 구현됨 |
| 캠퍼스 분류·WISE 차단 | src/services/campus_scope.py, api/rag_service.py, tests/test_campus_scope_safety.py | 분류·차단 구현됨. WISE 명시 질문의 즉시 거절도 구현 |
| 출처 identity·hash | src/services/source_contract.py, tests/test_canonical_payload.py | 구현됨 |
| 출처·citation 응답 | AskResponse, SourceChunk, golden_result.schema.json | 구현됨 |
| 학년도·상대 날짜 | src/utils/academic_period.py, src/services/temporal_context.py, 관련 테스트 | 구현 중 |
| 신청 마감일 hard filter | api/rag_service.py의 active notice filter, tests/test_active_notice_status.py | 구현됨 |
| 근거성·질문 관련성 | src/services/grounding.py, tests/test_grounding_guard.py | 구현됨. 검사 장애 시 fail-closed 여부는 별도 점검 필요 |
| 근거 선택 거절 | src/services/evidence_selector.py, tests/test_selector_refusal.py | 구현·실험 중 |
| 정본-검색 lineage | src/services/canonical_lineage.py, docs/canonical-lineage-runbook.md | readiness gate로 구현됨 |
| 정규화된 answer_state | 현재 AskResponse의 fallback_reason 등으로 부분 표현 | 후속 API·Golden 계약 작업 필요 |
| 승인된 공식 URL allowlist | 크롤러별 source 설정과 등록 절차로 분산 | 별도 source registry 작업 필요 |

이 문서는 위 표의 “후속 작업 필요” 항목을 이미 구현되었다고 주장하지 않는다. 정책
기준과 현재 구현의 차이를 숨기지 않고, 다음 변경의 범위를 고정하기 위한 문서다.

## 13. 계약 테스트 분류

Golden·단위 테스트는 다음 분류를 최소한 포함해야 한다.

    basic
    ambiguous
    date
    historical_info
    nonexistent_info
    department_or_facility
    typo
    cross_domain
    not_answerable
    personal_data_unavailable
    wise_boundary
    audience_boundary
    conflicting_sources
    prompt_injection_in_document

자료 부재(not_answerable)와 개인정보 거절(personal_data_unavailable)은 서로 다른
정답과 후속 안내를 가져야 한다. 한쪽의 답변 키워드를 다른 쪽 테스트에 재사용하지
않는다.
