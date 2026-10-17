# Dongttok Current Status

Last Updated: 2026-10-18 (정보 연결 후보의 독립 검토·확대 데이터 검증 및 후속 수정)

이 문서는 현재 작업과 다음 실행 순서의 요약이다. 코드·로컬 검증 기준이며,
운영 서버의 실시간 상태나 배포 승인 여부를 대신하지 않는다.
설정 상태는 코드 기본값을 기준으로 기록한다.
아래 9월 운영·RAG 기록은 이번 UI 작업에서 다시 확인하지 않은 당시 기록이다.

## Current Goal

동똑이 RAG 검색 품질과 서비스 안정성을 개선한다.
질문에 맞게 구조화 SQL 관계 조회, 키워드 검색, 벡터 검색을 선택하고,
동국대학교 공식 출처와 정본 데이터에 근거한 답변을 유지한다.

서울·바이오메디·공통 공식 공개정보의 수집 범위를 넓히고, 기존 정본과 새 자료를
제도·절차·자격·조건·기간·서류·교과목 등 **정보 내용과 근거 관계**로 연결하는 후보를 구현 중이다.
캠퍼스·대상·학번·학기·유효기간을 보존한 정보 단위와 출처를 공통 검색에 연결한다.
현재는 별도 worktree와 임시 검증 DB에서 진행하며, 실제 전체 수집·운영 반영 완료 상태가 아니다.

대화 기억·이전 문서 참조 수정(PR #80·#82)을 human 승인 후 main에 병합하고 운영 RAG에 반영했다.
공개 게스트 API에서 동일 문서 재질문·직전 질문 회상·다른 채팅 격리를 확인했다.

RAG 503을 운영에서 복구했고, 재발 방지 PR #73을 human 승인 후 main에 병합했다.
기존 primary 경로에서 별도 override 없는 Compose 전체 기동과 외부 게스트 채팅을 확인했다.

10월 13일 화면 감사에서 확인한 모바일 입력·알림 설정·관리자 화면 문제를
우선순위에 따라 개선했다. main 병합, 운영 웹/API 및 TestFlight 1.0(8) 반영을 마쳤고,
iOS 26.5 시뮬레이터에서 테스트 계정 로그인 후 설정·관리자·실제 채팅 저장/복원 검증까지 완료했다.

## 2026-10-18 공식 정보 수집·정보 단위 지식 그래프 (In Progress)

### 최신 상태: 확대 데이터 검증과 코드 보완, 병합 보류

- 원격 코드 PR #85는 여전히 draft의 구버전 `a456d8f`다. 최신 main `7db438c`는
  **기능 branch 안으로만** 병합해 로컬 HEAD `fedd213`으로 통합했다. primary main이나 운영 DB는 변경하지 않았다.
  아래 76파일 고정 이후에도 로컬 소스 보완이 진행 중이므로, 현재 수정본은 아직 새 코드 head로 게시하지 않았다.
- 전체 기능 76파일을 base `7db438c`에서 patch SHA-256
  `605e28a64cdfe28f9a39b519fde0a3ac2de43225322d2725497aa41894cb1e7f`로 고정하고 별도 검토 사본에 옮겼다.
  이 사본의 Root 일반·빈 DB·무작위(`8411223`) 검사는 각각 **2,650 passed / 1 skipped / 경고 10개**다.
  프런트 lint·165 tests·build와 전체 76파일 임시 Git index의 공백·secret 검사도 통과했다.
  실제 index와 원본 파일을 보존했고, 이 결과를 이후 변경된 소스의 출시 근거로 전용하지 않는다.
- 해당 76파일의 독립 검토는 **revise**다. Root도 실제 canonical hash 함수와 현재 검증 함수를 사용해
  세 반례를 재현했다. 원문에 보류 구간이 있어도 unit와 assertion 양쪽의 `source_coverage`를 누락하면
  발행 검증을 통과하는 경우, reference의 문서·URL·locator·인용문을 바꿔도 FK/revision만 맞으면
  통과하는 경우, 현재 revision/generation을 주장하는 검색행이 본문과 다른 `rule_unit_keys`로
  재승인되는 경우다. 현재 생성기가 해당 변형을 실제로 만들었다거나 운영에서 오답이 노출됐다는 판정은 아니다.
  새 참조 종류와 정상 부분 자료·원문 인용을 유지하며 각 검증 경계를 보완 중이다.
- 새 출처 라벨의 프런트 소스 검사는 통과했지만 추적되는 `wwwroot` 정적 출력은 아직 갱신하지 않았다.
  저장소 Dockerfile은 npm 빌드 없이 dotnet publish로 정적 출력을 복사하므로, 이 이미지 경로의 라벨 반영도 남는다.
  실제 Vercel 화면의 상태를 관측한 판정은 아니다. RAG 수정 종료 후 Client 범위에서 확인한다.
- 코드 검토 반례를 확인한 뒤 동일 76파일의 기능 ON 반복 검사를 Root가 명시적으로 중단했다.
  31파일 묶음은 **45 passed 후 SIGINT, exit 2**이며 전체 통과가 아니다. quietness나 시간 초과로 종료한 것이 아니고,
  앞서 완료된 일반·빈 DB·무작위 검사와 원본 영수증을 보존했다.
- 새 로컬 데이터 v3 후보는 독립 DATA QA 확인 기준으로 **12,614개 정본문서**다. 기존 12,449개 key와 값은 유지하고
  지원 공지 본문 159개와 정확한 부모 증거를 확인한 guide 6개를 추가했다. 기존 key 변경·삭제는 0개로 기록됐다.
  9,186파일의 output manifest SHA-256은 `d84530b01771811ee61ab852ae6b3b705e5a1e942bbe924d6c164db8862edafa`다.
  별도 읽기 전용 DATA QA가 시작·종료의 9,186파일과 코드 76개, 기존 12,449행 전 값,
  기존 archive 15개 테이블의 전 행 논리 digest 보존을 확인했다. 실제 신규 행의 provenance·부분 표시는 일치했으나,
  이 결과로 코드 검증 경계의 보류를 해제하지 않는다.
  운영 정본 ID를 확인한 결과나 승인된 데이터 발행을 뜻하지 않는다.
- 확대 사본의 의미 검증은 **미통과 / CODE_REVIEW_HOLD**다. 실제 corpus 70조회 중 첫 cold 요청이 중단됐고,
  자체 작업과 Root batch가 종료된 후 새 프로세스의 동일 5초 조회도 **5.756초 후 `OperationalError: interrupted`**로 끝났다.
  새 공지 holdout 32건은 대상 선택 23건·선택 누락 7건·미분류 2건이다. mock selector/generator/grounding을 둔
  실제 호출부의 원래 자동 검사는 18 passed / 6 failed이며 그 결과를 보존했다. 독립 DATA QA와 Root의 단계 JSON 대조에서는
  일부 누락 라벨이 거짓 양성이었다. 원문의 `평량평균 3.0 이상`, `2019 학년도 입학생까지 : 4.0 이상`,
  `편입생 및 캠퍼스이동 학생은 신청 불가`는 실제 selector·최종·grounding 입력에 있다.
  기대 문자열의 `평점평균`·공백·`캠퍼스간 이동`과 달라, 원문을 바꾸거나 중복 조건을 강제로 넣는 대신 라벨을 교정한다.
  source·단계가 없는 응답의 빈 오류 목록을 정상 근거 회수로 세지 않으며, 적용 범위·표현/로그 oracle·예산 한계도 대조한다.
  경찰 사이트 자료가 회계학과 질문의 후보에 포함됐다는 사실만으로 잘못된 의무나 부적격을 확정하지 않는다.
  실제 원문은 2024년 문서의 **2017학번 이후 대상·경찰 분야 네 전공·경찰사법대학 사무실 자필 제출** 지침이다.
  이 조건이 회계학과 일반 질문의 최종 문맥에 들어간 적용 범위 문제를 교정하며, 명시적 원문 비교·공통 자료는 유지한다.
- 새 RAG 후속 수정은 발행/참조/검색행 결합뿐 아니라 실제 12,614문서의 cold 조회와 조건 전달을 대상으로 한다.
  5초 제한을 늘리거나 source 검증을 생략해 통과시키지 않는다. 원본 v3와 과거 실패를 보존하고 새 소유 사본에서 재생성한다.
  기존 main/자동 Vercel 승인 질문은 계속 **중지/미응답** 상태이며, 실제 운영 사본·모델/사람 평가·전체 수집·승인 적용은 미완료다.

### 이전 고정 후보와 검토 이력

- 정보 연결 코드 후보는 [PR #85](https://github.com/won172/2025-2-OSSProj-renux-03/pull/85)이며,
  `test/integration-connected-information`의 `a456d8fb4b7cf17e9660088b180f03ad2c492996`다.
  검증 base는 `639c160`이고, 코드 PR은 draft/검토 단계다. main 병합·운영 데이터 적용·배포는 하지 않았다.
- 사용자 요구는 기존 데이터와 신규 공식 자료를 함께 수집하고, 제도·프로그램·절차의
  자격·조건·예외·기간·방법·서류와 명시적인 규정/교과목/원문 참조를 연결하는 것이다.
  각 정보 단위에 원문 URL·locator·revision·캠퍼스·대상·학번·학기·효력 범위를 보존한다.
  조직·연락처는 문의처 등 보조 항목이고, 같은 주제의 링크는 적용 조건을 상속하지 않는다.
- 범위는 사용자가 확인한 서울·바이오메디·공통이다. WISE는 제외하며 미확인 범위를 추정하지 않는다.
  `SourceDocument`가 정본이고 연결 표/FTS는 재생성 가능한 파생물이다.
  `RAG_CONNECTED_RETRIEVAL_ENABLED`와 `RAG_CONNECTED_MAINTENANCE_ENABLED`는 기본값 0이다.
- 후보는 공개 HTTPS 수집·receipt/hash/policy 검증·staging import·정보 단위 투영과 공통 ask/SSE,
  새 안내/첨부의 정확한 문서 재참조를 포함한다. 불확실한 표 열 관계는 검토 대상으로 분리하고,
  혼합 문단은 원문과 조건을 유지하면서 미입력 필드가 근거가 아님을 selector/최종 문맥에 표시한다.
  공개 갱신은 기존 권한·적용 범위·curated 본문을 넓히거나 비활성 문서를 재활성화하지 않는다.
- 핵심 `03a40b1`, 출처 카드 2줄 `58597c5`, 최신 main의 대화 기억/문서 참조를 통합했다.
  최종 54파일 patch SHA-256은 `7571ae41d17c07a4a7f1b31928d3726ca2fc5cab08ec3f7f647a45250d443137`이다.
  Root는 커밋의 patch와 54개 blob이 독립 검토 사본과 같고 worktree가 깨끗함을 확인했다.
- 독립 read-only QA는 앞서 재현한 공개범위 덮어쓰기, 표/중첩 문맥 우회, 공유 학기 및 빈 항목
  문제와 URL 전송·미입력 표시·curriculum/offering 보존의 후속 수정을 검토했다.
  당시 54파일의 판정은 **merge / human_review_required**였다. 아래 후속 반례로 현재 병합 근거로 사용하지 않으며, main·운영 승인이 아니다.
  Root는 해당 JSON을 재검증했고, 이전 반례·고정 patch와 실제 증거를 별도로 보존했다.
- 이후 더 넓은 합성 corpus에서 특정 학부 신청서의 졸업대상·등록학기 조건이 선택 근거의 qualifier에 빠지는 경우를 재현했다.
  기존 `a456d8f`에 대한 병합 권고는 당시 고정 사본의 검토 기록이며, 현재 병합은 보류하고 같은 PR #85에서 수정·재검토 중이다.
  실제 모델 오답/운영 노출을 관측한 판정은 아니다. 기존 main/Vercel 승인 요청으로 이 구버전을 병합하지 않는다.
- 10월 18일 독립 검토의 59파일 중간 고정 후보(`c7656cf`)는 **revise**다.
  실제 공통 학칙에서 새 정보 단위가 제외한 WISE 전용 조항과 보류 표가 기존 `build_rule_chunks`의
  일반 규정 색인/검색으로 다시 들어가는 두 P1을 확인했다. Root도 현재 수정 소스에서 WISE 청크 5개,
  보류 성적표 청크 1개와 졸업 관련 제55조 청크 4개가 shared로 취급되고 부분 출처 표시가 없음을 재현했다.
  기존 규정 검색·색인·선택·최종 문맥까지 같은 경계를 적용하는 수정이 진행 중이다. 원문 전체는 보존한다.
  중간 사본 이후 포괄적 질문 표현과 적용 문맥 검사도 추가돼, 해당 사본의 결과를 최신 수정본에 전용하지 않는다.
- 이후 구현 종료와 21개 인계 파일 해시 일치를 확인하고 전체 기능 70파일을 고정했다.
  patch SHA-256 `c9c3b738f5dd431bfd3d97f87a3ee5e9f1441203a0e85593b2b9721dd3bbf69d`, 3,039,005 bytes다.
  기존·신규 규정 경로의 캠퍼스 조항·보류 표·조건 보존은 이 사본에서 검증했으나 독립 검토는 **revise**다.
  API 요청의 BMC/필동 별칭 범위 누락과, 현재 원문에 옛 검색행의 제목·게시일·학년도·대상 표시가 남는 경우를 Root도 재현했다.
  정확한 K 원문 후보 자체를 계약 위반으로 본 지적은 추가 검토에서 축소·철회했다. 조건을 붙인 원문 설명·비교는 유지하며
  타 단과대학에 K 신청서 의무를 부여한 실제 답변은 미확정이다. 원본 검토와 교정 부록을 함께 보존했다.
  현재 요청 범위·정본 메타데이터·시점 계약을 후속 보완 중이므로 이 고정 사본도 병합 승인이 아니다.

### 로컬 검증 범위

- 후속 요청/출처/시점/assertion 수정의 72파일 사본은 patch SHA-256
  `1498669bd568d490a225afcf844c9255fad0f90a407552d81179054f3a9db04a`로 별도 고정했다.
  old ignored 데이터가 없는 깨끗한 사본의 Root 일반 전체 검사는 **2 failed / 2,565 passed / 1 skipped / 경고 10개**다.
  학업 가이드의 정규화 출처 locator가 파일명 대신 임시 identity hash로 바뀌는 경우와,
  규정 수집 후 `Chunk.rule_id`에 연결된 정본 ID 집합이 비는 두 기존 계약 회귀를 확인했다.
  나머지 빈 DB·무작위·기능 ON 실행은 실패 후 중단해 **미실행**이다. 구현 sandbox의 bind나 아래 과거 산출물 수집 오류와 구분한다.
  같은 branch에서 이 두 경로와 기존 정보 탐색 공백을 함께 보완 중이며, 이 사본은 병합 가능한 상태가 아니다.
- Root의 70파일 고정 사본 전체 일반·빈 DB·무작위(`8411223`)는 각각 **2,455 passed / 1 skipped / 경고 10개**,
  연결 기능 ON의 관련 26파일 검사는 **1,088 passed / 경고 10개**다. 네 실행 모두 exit 0이고 소스·검토 사본 70개 해시가 유지됐다.
  외부 통신과 작업공간 밖 쓰기를 제한하고 loopback을 검증한 호스트에서 실행해, source sandbox의 기존 bind 실패도 통과했다.
  Python·159개 패키지 버전을 구현 환경과 맞췄고, 전역 dotenv 비활성화로 안전한 임시 테스트를 막지 않았다.
  secret scan·whitespace 검사와 추가 70파일 secret 패턴 검사도 통과했다. macOS 임시 파일 경로 제한은 QA 전용 scratch 배치만 보정했고
  스캐너 패턴·소스·테스트를 약화하지 않았다. 이 결과는 c9 사본에 한하며 후속 수정본으로 전용하지 않는다.
- 아래 54파일의 전체 통과 수치는 구버전 고정 사본의 증거다. 현재 수정본의 전체 통과를 뜻하지 않는다.
  새 경로 집중 검사와 39개 본문/적용 조건 회귀를 실행했고, 후속 독립 검토의 일반 규정 우회를 수정 중이다.
  59파일 중간 사본 Root 전체 검사는 2,305 passed / 2 failed / 1 skipped였으며 두 실패는
  Root 실행기의 `PYTHON_DOTENV_DISABLED=1`이 환경 로더 테스트의 안전한 임시 fixture를 막은 결과였다.
  그 설정만 제거한 동일 두 테스트는 2/2 통과했다. 테스트 코드는 바꾸거나 제외하지 않았다.
  수정 소스와 사본의 차이가 확인돼 중간 Root 실행은 종료했고, 최종 사본의 전체 검증은 별도로 필요하다.
- Root의 최종 54파일 사본에서 전체 일반 및 무작위(`8411223`)가 각각
  **2,274 passed / 1 skipped / 기존 경고 10개**다. 연결 기능 활성화의 19파일 회귀는 **668 passed**다.
  기존 skip은 복사하지 않은 실제 공지/lexical 산출물이 필요한 검사다.
  source agent의 전체/flag 실행은 기존 localhost bind 권한 제한 1건으로 실패했으나,
  Root의 loopback 가능한 host에서 해당 테스트를 포함한 전체 및 flag 검사가 통과했다. 테스트를 약화/제외하지 않았다.
- 실제 일반 안내 G·학과 공지 N·실제 HWP H와 기존 공개 CSV 사본을 합친 9,501문서에서
  원천 기반 자동 검사 **130/130**이 통과했다. 전공결정의 대상/예외·기간/게시일·신청 경로,
  외국인 신입생의 조건부 다음 학기 신청, 계획 첨부/제출서류 구분, 2025/2026 기간 비교를 포함한다.
- 새 코드로 캐시된 HTML·지원 첨부 **6,426개**를 오프라인 재추출했다.
  새 정본문서 2,253개와 비답변 다운로드 출처 부모 7개를 기존 사본에 넣어
  **11,758문서**, import 오류 0개를 확인했다. 표 검토·OCR·미지원·실패 상태는 별도로 남는다.
  이 수치는 운영 문서 수나 모든 문서의 적용 범위/답변 가능성을 뜻하지 않는다.
- Frontend의 동일 TSX blob은 lint·155 tests·build, Root 관련 32 tests가 통과했다.
  변경 없는 C#은 Release build 경고/오류 0개와 기존 계약 tests가 통과했다.
  staged whitespace·secret scan도 통과했고 DB·secret·생성 인덱스를 commit하지 않았다.
- 실제 모델, 후보 endpoint 190문항, 사람의 답변/관련성 판정 및 serving hybrid 검증은 미실행이다.
  골든 workflow의 구성/skip 성공을 실제 후보 평가로 보고하지 않는다. 이탈률 개선도 아직 측정하지 않았다.

### 수집·데이터 경계와 남은 작업

- 이미 발견한 공식 공지 상세 528개 URL을 원문 링크·HTTP 응답과 대조해 별도 보강 수집했다.
  URL이 겹치지 않는 다섯 사본의 HTTP 200 응답 528개 중 본문 추출 359·OCR 필요 138·기타 추출 실패 31이다.
  부모 원문 증거를 포함한 입력 파일은 각각 103·103·280·275·286개로 고정했다. 다섯 번째는 학사 단어가 없는 승인 공지도
  포함하도록 주제 선택을 넓히되, 기존 origin/path·원문 링크·속도·용량 제한을 유지하고 WISE로 명시된 frontier는 제외했다.
  원본 대기열은 변경하지 않았고 전체 수집 완료도 아니다. v3 데이터 후보는 앞의 세 사본 240응답과 기존 입력만 사용하며,
  네 번째 144응답(본문 106·OCR 34·실패 4)과 다섯 번째 144응답(본문 94·OCR 40·실패 10)은 다음 통합 입력이다.
  응답 수를 새 정본 문서 수나 서로 다른 정보 수로 세지 않는다.
  초기 OCR 필요 공지 64건의 본문에서 이미지 주소 99개를 확인해 원본 80개를 별도로 확보했다.
  HTTP 주소/허용 범위 검토 15개와 응답 크기 제한 4개는 실패 기록을 보존했다. 이미지 원본은 125·93파일 입력 사본으로 고정했다.
  크기·프레임·방향 확인을 통과한 79개를 외부 통신 없이 로컬 OCR로 처리했고, 77개에서 관측 3,224개를 얻었다.
  원본·출력 해시와 좌표 형식 검사는 통과했으나, 읽기 순서·표 열 관계·가림값·적용 범위 검토는 완료되지 않아 모두 비활성이다.
  한 시각 표본에서는 특정 대학원 조건과 안내용 화면 예시를 구분해야 함을 확인했다. 이미지 수나 OCR 관측 수를 정확도·공식 사실 수로 세지 않는다.
  후속 대용량 단일 이미지와 같은 호스트의 HTTPS 자원 후보를 별도 검토 입력으로 확보해, 초기 79개와 합친 OCR은
  94개 처리·92개 비어 있지 않은 출력·관측 3,899개다. 관련 원본 응답 후보 98개와 DNS 실패 1건의 기록도 보존했다.
  HTTPS 후보의 원래 HTTP 자원과의 동일성·시각/배치·개인정보·적용 범위를 승인한 결과가 아니며 모두 비활성이다.
  같은 URL이 10개 정본에 붙어 보류된 guide 6건도 정확한 안내문 identity·공개 상태·실제 응답 hash·본문 anchor를 대조해
  새 사본에서 사용할 부모 연결 근거를 확보했다. v2의 기존 보류를 소급 해제하지 않았고, 자식 본문의 관측일을 부모 시점으로 바꾸지 않는다.
- 공식 교내 목록의 188개 항목/179개 호스트를 수집 origin 발견에 활용했다.
  이 목록과 HTTPS 도달성 확인은 기관 내부 본문 전체의 coverage와 구분한다.
- 원본 공개 수집 27차 checkpoint(`2026-10-17T14:43:50Z`)는 extracted 2,260,
  pending 20,000, deferred 12,483, OCR-required 370, unsupported-layout 581,
  authority-review 20이다. 미승인 원천/구조 검토 1,817개도 남아 있다.
  frontier 한도 밖 작업은 metadata와 함께 보존하며 전체 수집 완료로 세지 않는다.
- 원문 확보를 늘리기 위해 실제 부모 본문의 다운로드 링크를 대조한 첨부 URL 144개를 별도 수집했다.
  본문 추출 108, 미지원 16, 추출 실패 18, HTTP 실패 1, OCR 필요 1로 모두 집계했다.
  144개 URL의 raw hash는 123종이므로 응답 수를 서로 다른 정보 수로 취급하지 않는다.
  부모 원문·policy·receipt/hash와 복사 파일 331개를 두 입력 버전으로 고정했고 정본 활성화는 하지 않았다.
  앞서 확보한 안내/정정문/계획 첨부의 실제 응답 17개도 별도 입력 버전으로 보존했다.
- 한글/공백 주소 오류가 있었던 공개 첨부 21개는 검토된 코드로 별도 staging에서 재수집했다.
  인코딩 오류 0개, 18개 추출, 3개는 응답 용량 제한으로 보류했다.
  원본 checkpoint나 운영 정본을 덮어쓰지 않았고, 새 capture의 정본 합성/검증은 별도 작업이다.
- 별도 현행 규정 capture는 상위법/폐지를 제외한 77분류 515개다. 명시적 WISE 142개를 제외한
  373개 검토 후보에서 추가 WISE 28개를 제외했고, 서울 103·바이오메디 9·공통 38·unknown 195개다.
  이 capture는 위 11,758문서 corpus에 모두 반영된 결과가 아니다.
- 기존 데이터 평가 입력은 **추적된 공개 CSV 9,498행의 사본**이며 synthetic row identity를 사용했다.
  운영 정본의 ID·전체 크기·신선도와 구분한다. human이 제공할 read-only 정본 사본이 필요하다.
  primary의 공개 원천 CSV 3개를 수정 없이 비교해 현재 courses 4,405행(추적본 4,395),
  rules 1,029행(추적본 520), guide 105행의 차이도 기록했다. 이 추가분은 아직 기존 평가 사본에 합치지 않았다.
  이후 현재 공개 CSV 7종 10,017행을 raw 필드·행 위치·crosswalk와 함께 archive했으며 자동 활성화하지 않았다.
  12,291문서의 별도 합성 검증 사본은 새 적용 범위 반례와 보류 조문을 남긴 상태여서 서빙 승인 상태가 아니다.
- 다음 작업은 기존 규정의 병렬 근거 경계 수정, 최종 고정 후보의 전체/독립 검증, 새 capture와 현재 공개 원천의 버전/출처 보존 합성,
  계속된 수집과 OCR/미지원 adapter·캠퍼스/대상 검토, 실제 모델/사람 평가 및 승인된 rollout이다.
  source 스냅샷·복구 지점·파생물 lineage 검증과 별도 human 승인 없이 운영에 적용하지 않는다.

### 새 합성 데이터 v2 (고정 코드 c9 / 의미 검증 revise·시험 산출물 변경 기록)

- 별도 ignored staging에서 기존 키 12,291개·fixture 9,498개와 원본 CSV/crosswalk/proof-gap 각 10,017행,
  rule input 515개·기존 receipt/history 14,216개 및 archive 11개 테이블 digest를 보존했다.
  새 실제 HTTP receipt 232버전/231URL의 처분은 신규 canonical 158·N 공지 갱신 1·raw/history 보류 66·guide 부모 identity 복수 보류 6이다.
  DIC PDF는 20MB 제한으로 receipt가 없고, ZIP retry는 HTTP 200/unsupported로 보존한다. OCR 2개·ZIP 내부 PDF 13개는 비활성 후보다.
- 파생 정보 단위 98,232개를 재구성했고, 작업자 검사에서 구조/identity/span/revision 307,021건, 정책/부모/raw/CRC 2,086건,
  실제 규정 69/65/120 legacy/connected 소비 경계 89건이 통과했다. 동일 revision·projection hash의 재구성과 파일 16,674개 봉인을 확인했다.
  output manifest SHA-256 `46af815efea4b03fe63011ba759cb6e36ee560ab3295aff4d57f4b304be2ea64`다.
  작업자 무결성 통과를 전체 의미 검증·서빙 승인으로 취급하지 않는다.
- 독립 읽기 전용 검토에서 봉인·기존 행·원문 span 보존을 확인했으나, assertion 60,747행 중 3,077행의
  revision과 qualifier가 연결 unit과 다른 P1을 확인했다. qualifier 갱신이 revision 열을 덮어쓰는 codec 오류이며,
  기존 구조 검사가 이 관계 불변식을 놓쳤다. 같은 기능 branch에서 열 동기화와 출판 전 검증·회귀를 보완 중이다.
  원본 v2는 그대로 보존했다. 새 소유 사본의 재구성 후 Root도 읽기 전용 SQL로 불일치 0행·orphan 0행을 확인했고,
  원천 정본 12,449행의 논리 digest는 전후 같았다. 이는 현재 진단 사본의 증거이며 최종 고정 코드·서빙 승인이 아니다.
- 이후 수정 worktree의 전체 pytest가 ignored v2의 `helpers/test_candidate.py`까지 자동 수집했다.
  중첩 테스트가 옛 JUnit·cache·임시 시험 파일을 바꿔 **현재 전체 산출물 봉인은 유지되지 않는다**.
  Root는 실행을 중단하고 16,674개를 재대조해 시험 산출물 불일치 109개·추가 파일 3개를 확인했다.
  원천·수집·archive DB와 입력/출력 manifest의 byte hash는 기존과 같고, 시험 파일 외 불일치는 0개다.
  기존 manifest·실패·실제 변경 목록을 보존했으며 시험 출력을 재생성해 봉인을 복구했다고 주장하지 않는다.
  위 독립 봉인 검토는 이 사건 이전의 기록이다. 원천과 시험 산출물을 분리하고 새 깨끗한 사본에서 회귀를 재실행 중이다.
- 변경 없는 18일 oracle은 151 통과/2 실패, 최초 추가 검사는 3 통과/1 실패로 보존했다.
  실제 K 양식이 있는 전체 corpus에 전역적 서류 부재를 적용한 가정과 plan facet을 요청하지 않은 검사 역할 문제를 구분하고,
  별도 명시적 HWP/N→H source-bound 추가 검사의 13 통과 기록도 남겼다. 과거 oracle을 고쳐서 통과로 만들지 않았다.
- 관측이 18일 KST이고 게시일 미상인 새 문서 40개가 as_of 17일에서 eligible인 현상을 보고했다.
  새 평가는 18일을 사용했으며 17일에 알려졌다는 근거로 삼지 않았다. 관측·게시·적용·버전·학번 시점의 계약을 검토·보완 중이다.
  장학금·학점 인정·조기졸업은 실제 관련 원문이 있으나 질의에서 정보를 찾지 못하는 공백이 남아 있다.
  assertion만 교정한 대조에서도 결과가 같아, 이를 자료 부재나 codec 수정으로 해결된 문제로 해석하지 않는다.
  현재 정보 연결·질의 facet을 별도 읽기 전용 검토로 구체화하고 있으며, 일부 외국인 입학 조건도 미확정이다.
  새 코드·파생 재평가는 새 버전으로 만들고
  이 봉인 사본의 해시·판정을 소급 변경하지 않는다. 운영 ID·모델/사람·준비도·승인 적용은 미확인이다.

## 2026-10-17 대화 기억·문서 후속 질문 운영 반영 완료

- Human의 "반영해" 승인 후 [PR #80](https://github.com/won172/2025-2-OSSProj-renux-03/pull/80)
  (`c97c9f0`, merge `707ecc9`)·상태 문서 PR #81을 병합했다. 운영 검증에서 추가로 확인한 문서 참조 오류는
  [PR #82](https://github.com/won172/2025-2-OSSProj-renux-03/pull/82)
  (`b03b02d`, merge `a843d08`)으로 수정·독립 검증 후 같은 승인 범위에서 반영했다.
  primary를 fast-forward했고 기존 데이터·vectorizer·staff review 파일 25개의 byte hash를 보존했다.
- Redis 이력 초기화의 `'dict' object has no attribute 'index'`와 요청마다 새 임시 이력을 만드는 경로를 수정했다.
  dict/list 응답 호환 어댑터는 기존 JSON·키·인덱스를 유지하며, 세션별 bounded fallback·안정적인 메시지 ID·
  원자적 저장·원래 TTL로 장애 복구와 lost-ack 재시도의 중복을 방지한다.
- 명시적 후속 질문, 문서 번호·제목 참조, 짧은 조건 질문과 최신 학번·학기·캠퍼스·학과 정정을 연결한다.
  grounding 전 생성 후보는 이력에 저장하지 않으며, 검증된 최종 답변에만 출처 단서를 보존한다.
  실패·미확인 후보의 사실은 다음 답변의 근거로 재사용하지 않는다.
- 첫 운영 검증에서 제목이 `통계학과 - 이**`처럼 일반적이면 `[문서1] 자세히 알려줘`가 `out_of_domain`으로
  차단됨을 확인했다. PR #82는 선택한 자료의 데이터셋·문서·청크 식별자를 함께 전달하고, 현재 정본과
  projection의 동일 문서를 재조회한다. 현재 공개 상태·캠퍼스·학과 권한·학기·학번을 다시 검사하며,
  문서가 없거나 범위를 벗어나면 다른 동명 자료로 확장하지 않는다. Korean staff의 `부서경로`와
  `조직(트리)`에 있는 WISE 구분도 독립 회귀 검사를 통과했다.
- 최종 full-main 소스의 빈 DB·무작위 순서(`20261017`) 전체 검사는 각각
  **1,925 passed / 1 skipped / 기존 경고 10개**다. 독립 QA의 집중 **305 passed**와 추가 합성 8개 그룹,
  이전 Redis 격리 smoke·합성 9건이 통과했다. PR #80·#82의 frontend/backend/RAG 일반·빈 DB·무작위/secret CI도 통과했다.
  skip은 기존 ignored 공지 chunk·lexical 자료가 필요한 deadline recall 1건이다.
- 운영 런타임 baseline `0d6fa13`에 검증된 수정 `c97c9f0`·`b03b02d`만 적용한 이미지를 사용했다.
  무관한 최신 main 수집 로직을 함께 배포하지 않았고, 의존성·entrypoint·기존 설정을 보존했다.
  이미지의 전체 테스트는 **1,669 passed / 1 skipped / 기존 경고 10개**, 독립 패키지 QA는 **305 passed**다.
  full-main 1,925개와 이미지 1,669개는 baseline에 따른 서로 다른 검사 집합이다.
- 최종 이미지 `renuxserver-rag-service:conversation-memory-b03b02d`의 ID는
  `sha256:8f3955741b310ed8a3b672a9092ec11799e28e76cac38bd3d05b8c9849e095d4`다.
  RAG 서비스만 교체했고 `RAG_BUILD_REVISION=memory-b03b02d-on-0d6fa13` 외 환경과 모든 mount를 보존했다.
  처음 배포 검증 도구가 mount 배열 순서를 오인해 한 번 자동 복구했으며, destination 기준 비교로 수정했다.
  이후 최종 rollout은 healthy로 완료됐다. API·PostgreSQL·Redis·nginx의 ID·이미지·시작 시각은 기존과 같다.
- 2026-10-17 공개 `api.dongttok.com`의 실제 게스트 SSE에서 연락처 질문 → `[문서1] 자세히 알려줘` →
  직전 질문 확인과 별도 채팅의 회상 요청까지 **4건** 모두 HTTP 200, error 0, completion/done 각 1개였다.
  앞의 두 답변은 `grounded=true`·`verification_status=passed`이며, 재질문 출처 1개는 선택한 원래 문서 ID와 같다.
  직전 질문의 원문을 회상하고 별도 방에서는 이전 질문이 없다고 응답했다. 실제 모델의 운영 표본이며 전체 품질 평가가 아니다.
- 새 별도 프로세스에서도 같은 방의 Redis 메시지 6개와 출처 단서 3개/1개를 읽었다.
  배포 전에 기존 codec으로 작성한 검증용 메시지 2개도 유지됐다. 최종 컨테이너 로그의 기존 index 오류·
  Redis fallback 경고는 0개다. 직결/공개 `/ready` HTTP 200, 6개 데이터셋 strict lineage ready·위반 0개다.
- 최종 전후 비교에서 정본·도메인·ontology 13개 테이블의 row count·내용 digest와 정본 문서 17,947개,
  검색 관련 정적 산출물 63개의 hash가 같았다. DB migration·재색인·데이터 삭제는 수행하지 않았다.
  검증 질문에 따른 대화·query log 추가가 있으므로 DB 전체 byte 동일을 주장하지 않는다.
- 증거는 저장소 밖 `../dongttok-worktrees/.codex-runs/conversation-memory-release/`에 보존했다.
  secret 값·DB dump는 Git이나 증거 파일에 저장하지 않았다. 병합된 구현·QA·배포 worktree/local branch는 정리했고
  remote branch와 다른 작업의 worktree는 보존했다.
- 최근 20턴을 조회하고 프로세스 로컬 완충은 1,000세션·유휴 1시간으로 제한한다.
  Redis 장애 중 미동기화 이력은 프로세스 재시작·유휴 만료·용량 퇴출 시 유실될 수 있다.
  PostgreSQL 화면 기록에서 이미 유실된 RAG 이력을 복원하는 기능은 포함하지 않는다.
  실제 후보 190문항과 물리적 기기 검증은 미실행이며, endpoint 미설정 골든 gate의 skip을 출시 근거로 세지 않는다.

## 2026-10-16 RAG 503 운영 복구 완료

- Human의 실제 서버 복구 요청과 PR #73 병합 승인 후 작업했다. 코드 `5a11dd6`·`65a3179`의
  [PR #73](https://github.com/won172/2025-2-OSSProj-renux-03/pull/73)을 `0d6fa13`으로 main에 병합했고,
  primary를 fast-forward했다. 무관한 기존 RAG 데이터 변경은 보존했고, 복구 대상 파생물만 백업 후 교체했다.
- 현재 Chroma 마운트는 `/app/artifacts/db_chroma` → primary `src/RAG/artifacts/db_chroma`로 올바르다.
  기본 포인터의 최신 공지 컬렉션은 15,068개 청크가 있고, 예전 복구 컬렉션은 14,994개였다.
  오래된 컬렉션으로 되돌리지 않고 최신 자료의 파생물 불일치를 복구했다.
- 두 원인을 수정했다. 관리자 공지 갱신이 Parquet/lexical revision만 바꾸고 dense metadata를 남기는 경로를
  공통 refresh와 maintenance lock으로 통일했다. 학식 로더는 게시 가능한 `active`·`updated`를 모두 읽고,
  완전한 다음 수집에서 빠진 두 상태의 문서를 삭제 없이 숨긴다.
- RAG를 중지하고 SQLite 정본과 active artifacts 72개·약 2.2GB를 private 디렉터리에 백업했다.
  사본에서 공지 15,068개 벡터와 학식 기존 77개 벡터를 인증된 입력 hash로 재사용했고,
  누락된 학식 5개만 새 임베딩했다. 정본 DB는 사본 복구 전후 byte hash가 동일했고,
  실제 적용도 파생 artifacts만 교체했다. 이전 Chroma와 별도 백업은 보존했다.
- 독립 read-only QA는 6개 데이터셋의 정본 문서·청크·doc ID·revision·URL·FTS 일치와 무결성을 재확인했다.
  공지는 6,323문서/15,068청크, 학식은 82문서/82청크다. 나머지 4개 데이터셋의 파일과 dense/FTS 내용은
  백업과 같았다. 기존 staff 표현 차이는 이번 복구의 회귀가 아니며 별도 재색인을 하지 않았다.
- 실제 `src/RenuxServer`에서 **`docker compose up -d --build`**가 exit 0으로 완료됐다.
  최종 5개 컨테이너의 compose 설정은 기본 `docker-compose.yml` 하나이고 restart 정책은 모두 `unless-stopped`다.
  DB·Redis·RAG·API가 healthy이며 nginx를 통한 로컬/외부 `/ready`도 HTTP 200이다.
  최종 RAG 이미지는 `sha256:a77b1fd17cf7f61a83e54eef405c60976e8ffebd311262f80c30b0f554deb8f6`,
  API 이미지는 `sha256:7e698d9062efc9ab7349c05603c1651f457a022f9f3f42da89d5dccf5c605c8b`다.
- 외부 `api.dongttok.com` 게스트 세션에서 학식·학생지원팀 연락처 질문 각각 HTTP 200,
  completion/done 1개씩, error 0개로 끝났다. 학식은 결정적 응답 `verification_status=not_required`,
  연락처는 `grounded=true`·`verification_status=passed`·hybrid·degraded dataset 없음이다.
  두 표본의 운영 검증이며 전체 답변 품질/골든 평가로 일반화하지 않는다.
- 새 Dockerfile 이미지 전체 RAG **1,523 passed / 1 skipped / 기존 경고 10개**,
  독립 QA 전체 1,523개·집중 67개, 신규 회귀 3개 base 실패→candidate 통과를 확인했다.
  PR #73의 frontend/backend/RAG 일반·빈 DB·무작위 순서/secret CI와 독립 리뷰도 통과했다.
- PostgreSQL custom-format 백업 117,233B를 폐기 가능한 네트워크 없는 PostgreSQL에 실제 복원해 비교했다.
  12개 테이블의 기존 793행 내용이 모두 보존됐고 migration은 11개로 같았다.
  최종 검증 채팅에 따른 product_events 2행 추가만 관측했다. 임시 복원 컨테이너는 정리했다.
- 운영 백업/증거는 저장소 밖 `../dongttok-worktrees/.ops-recovery/server-lineage-20261016-151549/`의
  접근 제한 디렉터리에 있다. DB·secret·artifact를 Git에 넣지 않았다.
  병합된 구현 worktree/local branch는 정리했고 remote branch는 보존했다. 물리적 전원 재부팅은 미실행이다.

## 2026-10-16 Compose 단일 명령 기동 후보 (운영 적용 전 기록)

- `fix/compose-one-command-startup`의 `0e0452d`, [PR #68](https://github.com/won172/2025-2-OSSProj-renux-03/pull/68).
  최신 `origin/main=1d669c0`에서 만든 별도 worktree에서 구현했다. 기존 primary의 RAG 데이터·산출물 변경은 보존했다.
- 일반 `src/RenuxServer`의 `docker compose up --build`가 복구 override 없이 RAG를 재생성하면,
  기본 공지 포인터가 없는 컬렉션을 가리켜 `/ready`가 503이 되는 문제를 확인했다.
  entrypoint는 명시한 포인터·정상 기본 포인터를 우선하고, 기본 대상이 없을 때에만 기존 복구 포인터의
  대상이 실제 catalog에 있는지 읽기 전용으로 확인해 프로세스 환경에 전달한다. 포인터·인덱스 변경이나 재색인은 하지 않는다.
- Redis → RAG, PostgreSQL/RAG → API, API → nginx의 healthy 의존성, API DB readiness healthcheck,
  5개 서비스 `unless-stopped`, 누락된 SQLite bind의 디렉터리 자동 생성 차단을 추가했다.
  실행 조건·환경변수 우선순위·재부팅 순서의 제약은 [Compose 시작 문서](compose-startup.md)에 기록했다.
- 실제 Dockerfile로 RAG/API 후보 이미지를 빌드했다. 새 RAG 이미지의 네트워크 없는 빈 DB 테스트는
  **1,509 passed / 1 skipped / 기존 경고 10개**다. skip은 ignored 공지 아티팩트가 필요한 기존 평가다.
  구현 agent의 관련 테스트 65개 및 전체 1,509개, 독립 QA의 관련 테스트 48개가 통과했다.
  실제 Chroma fixture·nonroot entrypoint에서도 경로 전달·오류 거부·종료 코드·파일 내용 보존을 확인했다.
- 폐기 가능한 PostgreSQL·Redis·실제 후보 API/nginx와 RAG HTTP readiness fixture를 사용하는
  격리 Compose에서 최초 기동 및 stop/up의 **2회** 기동을 통과했다. nginx → API → 임시 DB 준비 상태는
  HTTP 200이며 fixture의 포인터/catalog 내용이 그대로였다. 임시 컨테이너·볼륨은 정리했다.
  이 검증은 실제 운영 모델·검색 데이터·질문 품질 또는 Docker 엔진 재부팅 전체 검증을 의미하지 않는다.
- Root의 backend restore·Release 빌드(0 warnings/errors)·전체 계약 테스트, 출처 식별자 8 scenarios/160 assertions,
  shell·Compose 구성·secret·staged diff 검사와 독립 QA가 통과했다. primary 파일·운영 컨테이너·secret·DB는 변경하지 않았다.
- 당시 상태는 **Review**였다. 이후 PR #68·#69·#73 병합 및 운영 반영 결과는 위 복구 완료 기록을 따른다.

## 2026-10-15 모바일 UX 구현·검증 기록

- 작업 기준은 `origin/main`의 `b559b966`이다. primary checkout의 기존 RAG 데이터·산출물 변경은 보존했다.
- iOS 키보드: `fix/ios-keyboard-resize`의 `c145245`, [PR #60](https://github.com/won172/2025-2-OSSProj-renux-03/pull/60).
  Capacitor Keyboard Native resize와 중복 inset 방지 설정을 추가했다. root의 lint·78개 테스트와
  Xcode 시뮬레이터 빌드가 통과했다. iOS 26.5 로컬 API fixture에서 로그인 상태와 게스트 홈,
  여러 줄 입력·전송 버튼 탭·키보드 닫힘 후 여백 복원을 확인했다. 독립 정적 QA의 P0–P2 지적은 없었다.
- 출처 저장: `fix/chat-source-identity`의 `bb2c56a`, [PR #61](https://github.com/won172/2025-2-OSSProj-renux-03/pull/61).
  `source_ref`·`citation_number`를 저장·히스토리 DTO까지 보존한다. root의 Release 빌드와 전체 계약 테스트,
  출처 보존 8개 시나리오·160개 assertion이 통과했다. 독립 정적 QA의 P0–P2 지적은 없었다.
- 웹 화면: `fix/mobile-interaction-layout`의 소스 `a8d15e0` 및 웹 산출물 `e7b5a3b`,
  [PR #62](https://github.com/won172/2025-2-OSSProj-renux-03/pull/62). 밝은 설정/로딩 화면, 설정 분리·8건 단위 목록,
  모바일 목록/상세 전환, 교직원 갱신 diff와 승인 가드, 터치 조작·출처 카드·로딩 복구를 구현했다.
  독립 QA의 P2 6건을 수정하고 재리뷰에서 추가 P0–P2 지적이 없음을 확인했다.
  root와 QA 각각 109개 프런트 테스트·lint·타입/번들 검사를 통과했다.
- `test/mobile-ux-integration`의 `1b68481`에서 세 후보를 조합했다. 프런트 lint·109개 테스트·웹/native bundle,
  서버 Release 빌드·전체 계약 테스트, Xcode 시뮬레이터 컴파일이 통과했다. 웹 산출물은 재생성해도 Git diff가 없다.
- 실제 Chrome 360/390/1280px에서 로컬 API fixture의 production bundle을 확인했다. 설정 탭·저장·미저장 이탈 확인,
  독립 조회 오류·계정 접근, 승인 이력 링크, 학과 폼, staff 검토/차단, 출처 1/3 카드 연결, 관리자 카드·drawer 초점 복귀,
  개인정보 화면을 확인했다. 동일 fixture의 설정 첫 화면 높이는 390px 폭에서 14,737px → 1,280px다.
- API fixture와 화면 증거는 저장소 밖에 보존하며 운영 인증·데이터를 사용하지 않는다.
  시뮬레이터/fixture 검증을 실제 기기·운영 API·배포 검증으로 해석하지 않는다.
- Mac 잠금 해제 후 실제 API 주소를 사용하는 통합 앱 1.0(7)을 시뮬레이터에서 실행했다.
  실제 홈 브리핑 표시, 게스트 키보드 위 입력창·전송 버튼 노출, 화면 탭으로 키보드 닫힘과 여백 복원을 확인했다.
  이 단계에서는 테스트 계정 로그인을 기다렸으며 로컬 fixture·웹 검증을 통과했다. 최종 결과는 아래 네이티브 검증 절을 따른다.
  PR #60·#61·#62의 원격 frontend/backend/rag-python/secret-scan 검사는 모두 통과했다.
- 웹 precache는 1,248,456 / 1,250,000B로 여유가 작다. 공개 화면·React·라우터는 계속 precache하며,
  관리자 전용 코드·스타일만 지연 로드한다. 기존 예산을 완화하지 않았다.
- 이 단계의 후보 검증 후 human 승인을 받아 PR #64를 main에 병합했다. 운영 반영 결과와 제약은 아래 최신 기록을 따른다.

## 2026-10-15 TestFlight 1.0(7)

- `chore/mobile-ux-testflight`의 `6d36470`, [PR #64](https://github.com/won172/2025-2-OSSProj-renux-03/pull/64).
  최신 `origin/main`에서 만든 전용 worktree에 세 검증된 기능 후보를 조합하고 빌드 번호를 6 → 7로 올렸다.
  기존 통합 트리와의 직접 비교는 Debug/Release 빌드 번호 두 줄뿐이다.
- 배포용 iPhoneOS Release archive/export, 릴리스 사전 검사, 실제 API 주소·비fixture·서명 확인이 통과했다.
  독립 패키지 QA의 정적 검사도 통과했다. 리뷰 sandbox의 서명 신뢰 체인 확인 제약은 root의
  `codesign --verify --deep --strict` 통과 및 Apple 검증·처리 성공으로 별도 확인했다.
- Apple 업로드 검증과 업로드가 오류 없이 완료됐다. Build/Delivery ID는 `97114306-4c30-4040-998b-0bcdc317224a`다.
  App Store Connect API의 `processingState=VALID`, `internalBuildState=IN_BETA_TESTING`,
  기존 내부 그룹 ‘똑똑이’ 포함을 확인했고 웹 화면에서도 **1.0(7) · 테스트 중**을 확인했다.
- 한국어 TestFlight 테스트 안내를 추가했다. 기존 내부 테스터의 화면에 1.0(7) 설치됨이 표시되지만,
  이것을 실제 기기 UI 동작 검증으로 해석하지 않는다.
- IPA SHA-256: `9e164d148f42bf7d4b4650124df963c75322f4ddb2faea85ab47bfd9658a5c30`.
- 외부 테스트 그룹은 기존과 같이 빌드 0개이며, 외부 Beta App Review와 App Store 공개 심사는 제출하지 않았다.

## 2026-10-15 최종 코드와 TestFlight 1.0(8)

- 운영 반영 준비 중 API 컨테이너가 `renuxserver-api:native-stream-auth-d826ac9`를 사용하며,
  해당 `d826ac9`와 대응 클라이언트 `3d758c9`가 `main` 및 빌드 7 후보에 포함되지 않았음을 확인했다.
  향후 API 교체에서 기존 티켓 기능을 잃지 않고 iOS SSE가 이를 사용하도록 두 변경을 함께 통합했다.
- 최종 검증 코드는 `chore/mobile-ux-testflight`의 `45535b6`, [PR #64](https://github.com/won172/2025-2-OSSProj-renux-03/pull/64)다.
  통합 커밋은 `133ce1a`이며 출처 식별자와 인증 티켓 테스트 호출을 모두 보존했다.
- Root의 frontend lint·123개 테스트·웹/native 빌드, backend Release·전체 계약 테스트가 통과했다.
  독립 인증 계약 리뷰에서 보고할 P0–P2 구현 결함은 없었다. 원격 PR #64 CI도 모두 통과했다.
  실제 API의 비로그인 `/chat/stream-ticket`은 401, 앱 Origin의 티켓 헤더 preflight는 204였다.
  이 단계에서는 로그인 후 실제 네이티브 HTTP→SSE→저장 경로가 미확인이었고, 아래 최종 검증에서 확인했다.
- TestFlight 1.0(8)의 Apple 검증·업로드·처리가 성공했다. Build ID는 `d9788bcc-8fd8-4c69-800c-1a4252290483`이며,
  `VALID`, 내부 그룹 ‘똑똑이’의 `IN_BETA_TESTING`과 웹 화면의 ‘테스트 중’을 확인했다. 한국어 테스트 안내도 반영했다.
  IPA SHA-256은 `462231bccf837a3502ca12afb02375e172e351ff2e46a1545897d02626b5b2a1`이다.
- 웹/API 이미지 `renuxserver-api:mobile-ux-45535b6`를 만들었고 이미지의 웹 파일 39개가 후보와 일치했다.
  운영 API 환경값과 데이터 보호 키 볼륨을 그대로 보존하는 배포 구성을 확인했다. 원래 compose override가
  실행 시 주입되는 환경변수에 의존하므로, 이를 누락한 일반 compose 실행을 사용하면 안 된다.
  값은 기존 컨테이너에서 자식 프로세스 환경으로만 전달하며 파일·로그에 복사하지 않는다.
- 이 full-main 이미지는 실제 기동에서 기존 EF snapshot 불일치로 실패해 즉시 이전 정상 이미지로 복구했다.
  아래 DB 구조를 유지하는 운영 이미지로 다시 배포했고 공개 정상 응답을 확인했다.

## 2026-10-15 승인 후 1차 병합·운영 반영

- Human이 PR #64·#63 병합과 운영 웹/API 반영을 명시적으로 승인했다. PR #64는 `3082004`로 main에 병합됐고,
  #60·#61·#62도 MERGED로 확인했다. main의 frontend/backend/rag-python/secret-scan CI가 모두 통과했다.
- 웹은 Vercel production `dpl_DGneJJD1XWHHoqbb5KcMWr4GuqQB`가 READY이며 `www.dongttok.com`에 연결됐다.
  운영 모바일 화면에서 밝은 설정 화면을 확인했다. 기존 열린 탭은 새로고침 후 새 버전으로 전환됐다.
  웹 로그인 세션이 만료된 상태이므로 최종 운영 보호 화면까지 확인했다고 해석하지 않는다.
- 최초 API 기동 실패 원인은 기존 `90bab94` 변경의 snapshot에서 `DegradedDatasetsJson`이 `ChatMessage` 대신
  `ActiveChat`에 들어간 점이다. 기존 운영 DLL은 `02c37e1` 모델에 native-auth 수정만 적용한 빌드였다.
  이미지 태그의 `d826ac9`가 전체 모델·migration 버전을 대표하지 않았음을 독립 QA가 DLL/PDB 체크섬으로 확인했다.
- 1차 운영 이미지는 `renuxserver-api:mobile-ux-compatible-bb2c56a`,
  `sha256:7a3373a7e47e263b653b49df43dfb4a8ce040470ef6adfb1d2bdb16af323049c`다.
  이전 운영 모델·migration·의존성과 인증 수정은 유지하고, `bb2c56ad`의 출처 JSON 보존 수정 및
  `45535b6`의 웹 자산만 반영했다. full-main 전체를 그대로 배포한 이미지가 아니다.
- 독립 QA는 모델/DbContext/migration 24개가 운영 기준과 동일하고, Docker 입력 웹 파일 47개가 최종 코드와
  byte-identical임을 확인해 산출물을 APPROVE했다. root는 실제 이미지에서도 같은 47개 파일과 의존성 일치를 확인했다.
- 별도 internal Docker network와 폐기 가능한 PostgreSQL에서 Release 빌드(0 warnings/errors), 전체 계약 테스트,
  DB 연동 계약 테스트, API 기동을 확인했다. 출처 식별자 8개 시나리오·160 assertions가 통과했다.
  독립 QA의 전체 실행은 read-only 임시 파일 제한으로 중단됐으므로 root의 격리 DB 완주 결과와 구분한다.
- 당시 운영 API 컨테이너 `cef3dfc7354a704e7b9d65e1ed0f8739b6e44fae3c8bd59396f02a3db733f227`의 healthy 상태를 확인했다.
  공개 `/health`·`/ready` 200, 비로그인 stream-ticket 401, native Origin/header CORS 204를 확인했다.
  운영 환경값 30개와 Data Protection 볼륨을 그대로 유지했다. RAG·DB·Redis·nginx의 ID/이미지/시작 시각이 같고,
  PostgreSQL migration 이력은 기존 10개로 동일하다. 운영 DB migration·데이터 삭제는 수행하지 않았다.
- TestFlight 최신 확인값은 1.0(8) `VALID`·`IN_BETA_TESTING`이다. 공개 App Store 심사는 제출하지 않았다.
- 이 시점에는 Mac 잠금 해제와 앱 로그인이 필요했다. 이후 로그인 후 설정·관리자·native SSE는 아래 최종 검증에서 확인했다.
  물리적 iPhone 검증은 수행하지 않았으며 iOS 시뮬레이터 결과와 구분한다.
- 이 단계에서 발견한 full-main snapshot 불일치는 아래 PR #65 및 운영 DB 반영으로 해결했다.
  당시 사용한 호환 이미지와 현재 운영 이미지를 구분한다.

## 2026-10-15 API 모델 수정·운영 DB 반영 완료

- `fix/api-model-snapshot`의 `b748cf1`, [PR #65](https://github.com/won172/2025-2-OSSProj-renux-03/pull/65).
  `DegradedDatasetsJson` 스냅샷 선언을 `ActiveChat`에서 실제 모델과 같은 `ChatMessage`로 이동했다.
  DB 연결 없이 snapshot 존재 및 `Database.HasPendingModelChanges()`를 검사하는 회귀 테스트를 추가했다.
  runtime 모델·DbContext·기존 migration Up/Down·startup은 변경하지 않았다.
- 새 검사는 수정 전 exit 1로 정확한 모델 불일치를 검출했고, 수정 후 전체 계약 테스트가 exit 0으로 통과했다.
  root Release 빌드(0 warnings/errors), 온라인 restore, 전체 계약·secret/diff 검사 및 독립 QA 재실행을 통과했다.
  출처 식별자 8개 시나리오·160 assertions도 포함한다. 전체 계약 assertion 총수는 실행기가 출력하지 않는다.
- 최종 운영 이미지 `renuxserver-api:api-model-b748cf1`,
  `sha256:c6c8f749bc4dfb352d97194f065dfa750038b36c7a5c0a609379aeba1feee8ff`를 적용했다.
  격리 internal network의 폐기 가능한 PostgreSQL에서 기존 운영과 같은 migration 10개 상태를 만들고,
  테스트 사용자·대화·출처를 저장한 뒤 새 이미지로 11개 상태까지 전환했다. 기존 행 수·내용 해시가 동일했고
  API health 200 및 DB 연동 계약 테스트가 통과했다. 기존 이미지로 되돌린 뒤에도 health 200과 추가 데이터 보존을 확인했다.
- Human이 PR #65·#66 병합과 운영 DB migration·API 반영을 명시적으로 승인했다. PR #65는 `2e319bb`로
  main에 병합됐으며 소스 `b748cf1`과 병합 결과의 파일 차이가 없음을 확인했다. 코드·문서 후보의 원격 CI는 모두 통과했다.
- 운영 PostgreSQL custom-format 백업 114,094B를 저장하고 `pg_restore --list`로 읽기 검증했다.
  이어 네트워크가 없는 임시 PostgreSQL에 전체 복원해 11개 테이블·753행의 내용 digest와 적용 전 migration 10개가 일치함을 확인했다.
  임시 컨테이너와 볼륨은 제거했고, 백업은 저장소 밖 private 디렉터리에 0600 권한으로 보존했다. 데이터 파일·인증값은 Git에 넣지 않았다.
- 운영에 `20260928010000_AddChatRetrievalModeAndDegradedDatasets`를 적용하여 `chat_messages`에
  nullable text 열 `retrieval_mode`, `degraded_datasets_json`을 추가했다. lock timeout 5초·statement timeout 30초를 둔
  트랜잭션이 성공했고 migration 이력은 10개에서 11개가 됐다. 데이터 삭제·열 이름 변경·Down migration은 실행하지 않았다.
- 추가된 두 nullable 열을 제외하고 적용 전후 11개 테이블의 기존 753행을 비교했다. 행 수와 내용 digest가 모두 같았다.
  이 값은 배포 직전·직후의 관측이며 이후 사용자 활동에 따른 데이터 변경까지 불변이라고 의미하지 않는다.
- API만 교체했고 컨테이너 `7fab9ea2686e3df975fba8c282aae65848ea6659316d1a21b58747cbe81ede1f`의
  이미지·소스 revision·healthy 상태를 확인했다. 공개 `/health`·`/ready` 200, 비로그인 ticket 401, native CORS 204였다.
  환경값 30개와 Data Protection 볼륨을 유지했고 RAG·DB·Redis·nginx의 ID/이미지/시작 시각은 그대로였다.
  이전 API 복구는 필요하지 않았다. 문제가 생길 경우 이전 API로 돌아가되 추가 열과 데이터는 보존한다.
- `api-model-snapshot` 구현 및 독립 리뷰 worktree/local branch를 병합 확인 후 정리했다. remote branch는 유지했고,
  primary checkout의 기존 RAG 변경 10개는 fast-forward 전후 해시가 같음을 확인했다.
- 최종 시뮬레이터 앱을 1.0(8)로 갱신했고 native public 파일 44개가 TestFlight 8 archive와 같음을 확인했다.
  이후 사용자가 테스트 계정으로 로그인해 아래 최종 화면 검증을 완료했다.

## 2026-10-15 최종 네이티브 화면·운영 채팅 검증

- 사용자가 직접 로그인한 iOS 26.5 시뮬레이터의 동똑이 1.0(8)에서 확인했다. 실제 운영 API를 사용했고,
  설치된 앱의 public 파일 44개는 TestFlight 8 archive와 동일하다. 이 결과를 물리적 iPhone/Android 검증으로 확대하지 않는다.
- 설정의 밝은 배경과 관심 주제·내 마감·알림함·계정 네 화면을 확인했다. 관심 주제의 로컬 초안을 바꾼 뒤
  홈 이동 경고와 ‘머무르기’를 확인하고 원래 값으로 되돌렸다. 저장 버튼은 실행하지 않았다.
- 마감 40건의 상세 펼침/접힘과 1/5 → 2/5 페이지 이동, 알림함 60건 조회·범위 설명·계정 화면을 확인했다.
  개인정보 문서는 화면 폭 안에서 줄바꿈되며 끝까지 세로 스크롤된다. 시뮬레이터 입력에서는 버튼 초점을 해제한 뒤
  터치 드래그와 렌더링 완료 상태를 확인했다. 즉시 캡처된 이전 위치만으로 스크롤 실패라고 판단하지 않았다.
- 관리자 대시보드, 검수함, 콘텐츠, 대화 로그, 피드백, 사용자·조직, 시스템, 학과 콘솔을 확인했다.
  메뉴 로고가 상태 표시줄과 겹치지 않았고 목록은 모바일 카드로 표시됐다. 39개 목록에서 새 FAQ/행사/공지 폼이
  즉시 상세 화면으로 전환되고 목록으로 돌아왔다. 등록은 제출하지 않았다.
- 실제 승인된 교직원 갱신 항목에서 현재/수집 수, 추가·삭제·연락처 변경 수, 생성 시각, 샘플 100개 한계와 변경 전후 정보가 표시됐다.
  관리자 승인·권한 변경·재인덱싱·스케줄러 실행, 알림 읽음/정리 작업은 수행하지 않았다.
- 게스트 여러 줄 입력과 로그인 후 답변이 있는 채팅에서 소프트웨어 키보드 위 입력창·전송 버튼을 확인했다.
  키보드 닫힘 후 여백이 복원됐다. 로그인 계정으로 ‘이번 주 학사일정 알려줘’ 한 건을 보내 실제 응답을 받았다.
- 서버 접근 로그에서 `/chat/start` 200 → `/chat/stream-ticket` 200 → native preflight 204 → `/chat/stream` 200을 확인했다.
  인용 ‘문서1’을 누르면 해당 출처 카드가 열리고 강조됐다. 후속 질문도 표시됐다.
- 테스트 대화 이름을 `Mobile UX QA 2026-10-15`로 바꾸고 새 대화로 나갔다가 목록에서 다시 열었다.
  `/chat/load` 200과 동일 답변·출처 복원을 확인했다. 해당 대화만 읽기 전용 DB 조회하여 로그인 사용자 소유,
  질문 1건·완료 답변 1건, 저장된 `sourceRef` 및 `citationNumber=1`을 확인했다. 테스트 대화는 삭제하지 않고 남겼다.
- 실제 운영 한 건은 인증·전송·저장·복원·인용 연결을 입증한다. 같은 URL의 여러 인용 구분과 오류·취소 경로는
  앞서 통과한 계약/fixture 검증으로 뒷받침하며, 이 한 질문으로 전체 RAG 답변 품질을 판정하지 않는다.
- 최종 main `9bbbb59`의 CI와 문서 PR #66 마지막 커밋의 CI도 모두 통과했다. 클라이언트 소스와 웹 자산은
  TestFlight 8의 `45535b6`부터 변경되지 않았다.
- `mobile-ux-testflight`와 `mobile-ux-audit` worktree/local branch를 병합 포함 여부 확인 후 정리했다.
  운영 재현용 `.ops` 산출물은 저장소 밖 증거 폴더로 이동해 보존했고 remote branch는 유지했다.
  초기 `test/mobile-ux-integration`은 별도 merge commit이므로 기록용으로 보존했다.
- 원래 화면 감사의 키보드, 설정 색상·긴 목록, 모바일 학과 폼, 교직원 diff, 개인정보 폭, 터치 조작,
  관리자 카드, 안전 영역, 출처 대응/자산 정합성 개선을 구현·배포·검증 완료로 정리한다.

## 2026-09-28 운영 수집 확인

- 마지막 성공 수집일부터 9월 28일까지 공지 9개 게시판을 경계 이전 페이지까지 확인했다.
  최종 실행은 167건을 확인했고 정본·Parquet·Chroma strict lineage가 통과했다.
- 공식 규정 전체 분류 514건, 학사일정 102건, 교과과정 4,405행, 생협·D-Flex 식단
  9월 21~28일을 수집했다. 식단의 9월 24·25일은 D-Flex 공식 PDF에 날짜 열만 있고
  메뉴 칸이 비어 있어 메뉴/휴무를 만들어 넣지 않았다.
- 교직원 1,096개 부서 4,566건을 수집했다. 변경 후보 #22(추가 805, 삭제 542,
  연락처 변경 54)는 승인 대기이며 기존 공개 명부 4,303건을 유지한다.
- 교과과정의 옛 `gt.dongguk.edu` 주소는 DNS 오류로 접근할 수 없었다. 해당 학과의
  기존 28행을 보존했고 공식 PDF의 최신 28행도 수집했다. 이 실행은 부분 성공이다.
- 운영 RAG `/ready`, 6개 데이터셋 strict lineage, SQLite 무결성·외래키,
  공지 품질 검사가 통과했다. 이전에 실패한 학사일정·공지 질문을 RAG `/ask`로
  재실행해 각각 출처 2개·3개를 포함한 응답과 근거성 결과를 확인했다.
- 당시 운영 스케줄러는 `RAG_SCHEDULER_ENABLED=0`이었다. 10월 16일 최신 확인값은
  기존 설정을 보존한 `1` 및 readiness `started`이며 위 운영 복구 기록을 따른다.

## System Status

| Component | Status | Notes |
|---|---|---|
| Frontend | Stable | 10월 15일 웹·TestFlight8 반영, lint·123 tests·build 통과. 최종 native 로그인 후 설정·관리자·채팅 검증 완료 |
| Main Backend | Stable | 10월 16일 최신 main 기본 Compose 이미지 기동. migration11·기존793행 보존, 외부 ready/SSE200 |
| RAG Server | Ready / Recovery Verified | 10월 16일 PR #73 적용, 6개 strict lineage·ready200, 외부 게스트 SSE2건 완료. 전체 답변 품질 평가는 별도 |
| Crawler | Scheduler Started / Next Cycle Pending | 10월 16일 기존 enabled=1 보존·readiness started. 복구 후 다음 실제 cron 수집 결과는 미확인 |
| SourceDocument | Active / Strict Lineage Passed | 운영 사본 및 운영 RAG에서 6개 데이터셋 정본·Parquet·Chroma 검사 통과 |
| Dense Retrieval | Active / Strict Lineage Passed | 최신 공지15,068·학식82 및 전체6개 lineage 통과. 공지/기존학식 벡터 재사용·학식5개만 임베딩. 전체 검색/답변 품질 평가는 별도 |
| Lexical Retrieval | Active | FTS5/BM25. SQL로 좁힌 규정 문서 내부 청크 재정렬에도 사용 |
| Reranking | Rule-based Active / Model Disabled | 최신성·필터·후보 융합 적용. Cross-encoder는 `RERANKER_ENABLED=0`이 기본값 |
| Ontology Retrieval | Scoped SQL Active / Broad Expansion Experimental | `RAG_STRUCTURED_RETRIEVAL_ENABLED=1`. 명시적 연락처·과목·학번 질문만 관계 근거 우선 조회. Shadow·광범위한 후보 보강은 기본 OFF |
| Grounding | Implemented / Quality Evaluation Pending | 공식 출처 기반 생성·근거성 검사 구현. `verification_status`(passed/failed/unavailable/not_required)를 RAG completion에 노출(PR #15). 외부 API SSE까지 필드 전달 확인. 전체 실제 후보 답변의 품질 통과를 의미하지 않음 |
| iOS | Simulator Verified / TestFlight Available | 1.0(8), iOS26.5 시뮬레이터에서 로그인·키보드·설정·관리자·운영 SSE/저장/복원 확인. 물리적 iPhone은 미검증 |

## Current Priorities

### P0

- 공지 갱신 코드와 스케줄러 시작 상태를 확인한 뒤, 다음 정규 수집의 ingestion run·
  strict lineage·freshness를 검증한다. 10월 16일 운영 503 복구와 전체 6개 lineage 검사는 통과했다.
- 실제 후보 endpoint의 전체 골든 평가와 결과·manifest를 확보한다.
  현재 후보 URL이 없으면 CI가 평가를 skip하므로, CI 성공만으로 출시 가능하다고 판단하지 않는다.

### P1

- 개선된 알림 설정·모바일 관리자 화면의 운영 사용성을 관찰한다. 기존 탭은 새로고침 후 새 버전 표시를 확인했다.
- 이미 구현된 regression 평가 도구를 같은 질문·기준일·corpus/model revision으로 반복 실행한다.
- 실제 질문과 사람 판정 qrels를 확대한다. 관계형 질문 목표는 200건이며 교과목 실사용 표본을 보강한다.
- Retrieval 품질과 cold/warm 지연, p50/p95, fallback을 함께 측정한다.
- SourceDocument/Parquet/Chroma 및 lexical revision 정합성을 변경·배포 전 검증한다.
- 공통 검색 계획 이후 남아 있는 근거 선택·생성·fallback 실행 코어를 통합한다.
- 단순 질문의 근거 선택 LLM 우회와 제한 동시성 검색을 회귀 평가로 검증한다.

### P2

- Ontology 후보 보강·cross-encoder 활성화는 사람 판정 및 실제 후보 품질·지연 gate 통과 후 평가한다.
- Parent document context와 청크 표현 품질을 개선한다.
- 물리적 iPhone의 별도 검증 범위를 정한다. iOS 26.5 시뮬레이터의 로그인·스트리밍·출처 저장/복원은 10월 15일 확인했다.

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
| 공식 정보 수집·정보 단위 지식 그래프 | P1 | RAG/Backend / Client / QA / Orchestrator | `test/integration-connected-information` ([PR #85](https://github.com/won172/2025-2-OSSProj-renux-03/pull/85)) | 핵심·통합·불변 QA·새 데이터 사본 별도 | In Progress | c9/70파일 Root 일반·빈DB·무작위2,455/1skip·flag1,088; 독립QA revise/교정 보존; v2 데이터 작업자 무결성 통과·의미 미통과 | 요청 별칭·현재 출처·시점·assertion codec 보완 중; Root 새 진단 사본 불일치 0행, 기존 자료 질의 공백 별도 검토. 전체 자료·운영 ID·모델/사람·운영 미완료 | 보완 후 새 파일 고정·전체/독립 재검증·새 데이터 버전 평가 후 human 검토 |
| 동일 채팅 이력·문서 참조·조건 정정 | P1 | RAG/Backend / QA / Orchestrator | `fix/conversation-memory`·`fix/document-reference-domain-context` | 정리 완료 | Completed | full-main1,925/1skip·이미지1,669/1skip·독립QA305·CI·공개SSE4건·Redis보존·6개lineage·13테이블/63산출물 무결성 | 최근20턴; 장애 중 미동기화 이력의 프로세스 유실 한계; 실제 후보190문항 미실행 | PR #80·#82 병합·운영 반영 및 문서 재질문 확인 완료 |
| RAG revision·학식 published 상태 503 복구 | P0 | RAG/Backend / QA / Orchestrator | `fix/rag-lineage-recovery` | 정리 완료 | Completed | 신규 회귀 3개·RAG 1,523/1skip·QA 67·CI·6개 strict lineage·실제 기본 Compose 기동·외부 SSE·기존 PostgreSQL 793행 보존 | 물리적 전원 재부팅·전체 운영 골든 평가는 미실행 | PR #73 병합·운영 적용·503 해소 완료 |
| Compose 단일 명령 전체 기동 | P0 | RAG/Backend / QA | `fix/compose-one-command-startup` | 정리 완료 | Completed | 후보 RAG 1,509/1skip·QA 48·CI, 최신 운영 기본 Compose 기동·ready200·외부 SSE 확인 | 엔진/물리적 전원 재부팅 자체는 미실행 | PR #68·#69 병합 및 primary·운영 반영 완료 |
| API 기동 EF 모델·스냅샷 일치 | P0 | RAG/Backend / QA | `fix/api-model-snapshot` | 정리 완료 | Completed | 회귀 실패→통과, Release/계약, 독립 QA·CI, 격리 DB 전환/복구, 운영 백업·migration11·기존753행 보존·health/ready200 | 운영 인증·UI 최종 검증 완료 | PR #65 병합·운영 반영 완료 |
| 모바일 UX·네이티브 인증 TestFlight 1.0(8) | P1 | Orchestrator / QA | `chore/mobile-ux-testflight` | 정리 완료 | Completed | 123 tests, backend 계약·독립 QA·CI, Apple VALID/IN_BETA_TESTING, native 설정/관리자/키보드, 운영 ticket·SSE·저장/재조회 확인 | 물리적 iPhone/Android 검증과 구분 | 구현·배포·최종 검증 완료; 증거 보존 |
| iOS 키보드 입력창 가림 | P0 | Client | `fix/ios-keyboard-resize` | 정리 완료 | Completed | lint, 78 tests, native build, iOS 26.5 fixture 키보드 검증, 독립 QA | 물리적 iPhone은 미검증; 운영 인증 시뮬레이터 확인 | PR #64 병합 및 native 키보드 검증 완료 |
| 저장 답변 출처 식별자·번호 보존 | P1 | RAG/Backend | `fix/chat-source-identity` | 정리 완료 | Completed | Release build, 전체 계약 테스트, 8 scenarios/160 assertions, 독립 QA | 동일 URL 다중 인용은 계약/fixture, 운영 저장·복원은 1건 검증 | PR #64 병합 및 운영 sourceRef/citationNumber 보존 확인 |
| 설정·관리자 모바일 사용성 | P1 | Client | `fix/mobile-interaction-layout` | 정리 완료 | Completed | 109 tests, lint/build, bundle budget, 독립 재리뷰, Chrome 360/390/1280px fixture, PR #62 CI 통과 | 관리자 실데이터 변경은 수행하지 않음 | 로그인 후 native 설정·관리자 화면 검증 완료 |
| 모바일 UX 초기 통합 | P1 | Orchestrator / QA | `test/mobile-ux-integration` | `mobile-ux-integration` | Review | 프런트 109 tests·웹/native bundle, 서버 Release·계약, Xcode sim build, build7 게스트 키보드 검증 | 이 트리에는 운영 native auth 보강이 없음. 최종 후보는 PR #64/45535b6 | 최종 릴리스 후보 증거를 기준으로 검토 |
| 공지 증분 동기화·관리자 upsert 임베딩 입력 통일 | P1 | RAG/Backend | `fix/notice-live-embed-retrieval-text` | `~/dongttok-work/notice-live-embed-retrieval-text`(임시, 아래 Git State) | Review | 로컬 RAG 1,453 passed/1 skipped(main 711744e 병합 후), QA 재리뷰 APPROVE, PR CI 대기 | 관리자 승인 학과 한정 공지가 Chroma에 public으로 들어가던 공개범위 결함 수정. 실제 데이터 검색 영향 미확인 | PR #51 human 검토·merge |
| 운영 공지 자동 갱신 후 회귀 확인 | P1 | Orchestrator | - | - | Review | 최신 main 이미지 적용·RAG ready200·기존 scheduler=1/started 확인 | 복구 후 다음 cron의 실제 결과는 미확인 | 다음 정규 수집 후 run·lineage·freshness 확인 |
| 실제 후보 골든 평가 190문항 (P0) | P0 | QA | - | - | Blocked | release gate는 fail-closed로 전환(PR #14) | 후보 endpoint·모델 비용 승인 필요 | human이 endpoint 제공 |
| Orchestrator·Codex 실행 설정 후보 적용 | P1 | Orchestrator | `chore/agent-orchestration` | `agent-orchestration` | Blocked | runner 단위 테스트 22/22(2026-09-27 재실행) | staged 상태. 에이전트 권한 설정 commit이 auto mode에서 차단됨. base `cffe3e3`로 오래됨 | human이 commit·PR 여부 결정 |

Worktree 경로는 `../dongttok-worktrees/<slug>` 기준으로 적는다.
완료된 행은 다음 갱신 때 Recently Completed로 옮긴다.

## Git State

2026-10-15 모바일 UX 작업 시작 시 `git fetch origin`으로 `origin/main = b559b966`을 확인했다.
신규 브랜치는 모두 이 기준에서 별도 worktree로 생성했다. human 승인 후 PR #64가 `3082004`로 병합되어 #60·#61·#62도 MERGED가 됐다. 세 구현 worktree와 local branch를 정리했고 remote branch는 보존했다. primary의 기존 RAG 변경 10개는 내용 해시가 그대로임을 확인했다.

2026-09-28 기준 확인값. 원격 상태는 `git fetch` 시점에 따라 달라진다.

- Remote: `origin` = `github.com/won172/2025-2-OSSProj-renux-03` (fork, upstream `CSID-DGU/2025-2-OSSProj-renux-03`).
- `origin/main` = `711744e` (2026-09-29 KST 확인, PR #50까지 merge, main CI 4개 job success). branch protection 없음(human 설정 필요).
- 새 task의 base branch는 `origin/main`이다. PR #13 이후 task PR은 STATUS.md를 수정하지 않는다.
- merge된 task의 worktree·local branch는 정리했다. remote branch(`chore/golden-release-gate`,
  `fix/grounding-verification-status`, `chore/build-lineage-gate`, `fix/chunk-representation`,
  `feat/official-rules-coverage`, `feat/notice-deletion-detection`, `docs/codex-executors`,
  `docs/status-batch-update` 등) 삭제 여부는 human 결정 대기.
- 규칙 도입 전 branch(`codex/*`, `auto/*`, `redesign/*`, `v2-agentic-graph-rag`,
  `docs/*-20260927`)는 정리하지 않고 유지한다.
- 구현은 Codex 우선(`codex` Pro Lite 구현, `codex2` Plus 읽기 전용 QA, PR #20). 계약·보고는 git 밖
  `../dongttok-worktrees/.codex-runs/`에 둔다.
- 2026-09-29 로컬 개발 환경 제약: Desktop 폴더(primary checkout·`dongttok-worktrees/`)에 대한 macOS
  파일 접근 권한이 agent 프로세스에 적용되지 않아, 임시로 `~/dongttok-work/`의 별도 clone에서 worktree를
  만든다. 같은 이유로 Codex 명령 도구가 타임아웃되어 PR #50·#51은 Claude subagent가 구현·QA를 대체했다.
  Desktop 쪽 merge 완료 worktree 정리는 권한 복구 후 수행한다.

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

### ISSUE-005 — Redis 장애 중 미동기화 이력의 내구성 한계

Impact: Medium

동일 방의 이력 초기화·문서 참조 결함은 PR #80·#82 병합 및 운영 반영으로 수정했고, 공개 API 표본 검증을 통과했다.
정상 Redis에서는 이력을 보존하지만 장애 중 완충은 프로세스 로컬이다. 재시작·유휴 만료·용량 퇴출 시
미동기화 이력이 유실될 수 있으며, PostgreSQL 화면 기록으로부터의 자동 복원은 포함하지 않는다.

Next Action:
후속 내구성 개선 시 PostgreSQL 기록과 출처 식별자에 기반한 복원 계약을 별도 설계·검증한다.
현재 운영 성공을 전체 질문 품질이나 장애 중 영구 저장의 증거로 해석하지 않는다.

### ISSUE-001 — 실제 질문의 정량 회귀 판정 근거 부족

Impact: High

평가 도구와 질문 세트는 있으나 대부분의 retrieval relevance 라벨이 자동 생성이며,
SQL 경로에서 바뀐 실로그 후보에는 사람 판정이 없다. 구조형 fixture 개선만으로
전체 질문의 검색·답변 품질 개선을 판단할 수 없다.

Next Action:
변경 후보 17건을 먼저 판정하고, 최근 질문·교과목 질문과 expected source를 보강한다.
자동 라벨과 사람 라벨의 평가 결과를 분리한다.

### ISSUE-002 — 자동 수집의 다음 운영 주기 확인

Impact: Medium / P1

9월 28일에는 공지 증분 revision 수정 코드의 운영 배포와 스케줄러 재개가 대기 중이었다.
10월 16일 실제 복구에서 최신 main 이미지와 PR #73을 적용했고, 6개 데이터셋 lineage 및
RAG `/ready` 200을 확인했다. 기존 운영 설정 `RAG_SCHEDULER_ENABLED=1`을 보존했으며
readiness의 scheduler는 `started`다. 이번 복구가 스케줄러를 새로 활성화한 것은 아니다.
복구 이후 다음 cron의 수집 성공·freshness 갱신은 아직 확인하지 않았다.

Next Action:
다음 정규 수집의 ingestion run, strict lineage, freshness를 확인한다. 시작 상태와
실제 수집 성공을 구분하며 누락 기간의 자동 소급 수집을 주장하지 않는다.

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

검색 계획(#30)과 실행 코어(#37 `execute_query`)는 공통화했다. 데이터셋 검색은 제한 동시성(#49,
`RAG_RETRIEVAL_CONCURRENCY` 기본 2)으로 실행하며, 합성 mock 벤치마크 p50/p95는 동시성 1에서
164.70/171.35ms, 2에서 89.05/99.94ms였다(9월 28일 로컬). 실제 endpoint·동시 요청의 p95는 미확인이다.

Next Action:
후보 환경에서 `scripts/report_stage_latency.py`로 단계별 p50/p95/p99를 측정한다.

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
- 9월 28일 프런트 fallback 사유 라벨 전체 매핑·추천 질문 요청 취소 (PR #21, codex 구현·codex2 QA).
- 9월 28일 RAG 중계 timeout 단계 분리(연결·첫 byte·무활동·전체), 저장은 base 의미 유지 (PR #22).
- 9월 28일 PWA precache 축소·bundle budget 검사 (PR #23).
- 9월 28일 fallback 사유 세분화: future_unannounced, stale_data, clarification_needed, out_of_domain, selector_refused (PR #25). fallback 비율 지표 상승 예상.
- 9월 28일 학수번호·규정번호를 학사시기로 읽는 버그 수정 (PR #26, 재색인 후 반영).
- 9월 28일 청크 표현 P1: staff·schedule 라벨 템플릿, 빈 공지 low_value (PR #27, 재색인 후 반영).
- 9월 28일 프런트 verification_status 표시·새 fallback 라벨 (PR #28).
- 9월 28일 retrieval_text 문서 제목 중복 제거 (PR #29, 재색인 후 반영).
- 9월 28일 공통 QueryPlan·`_plan_query` (PR #30).
- 9월 28일 verification_status·relevance_score 메인 서버 저장·이력 반환 (PR #31, **DB migration 포함, 운영 적용 별도 승인 필요**).
- 9월 28일 scheduler job DB lease(다중 replica 단일 실행) (PR #32, 신규 테이블).
- 9월 28일 검색 결과 trace·dense degraded 표시(검색 계층) (PR #34).
- 9월 28일 테스트 실제 네트워크 호출 제거·suite guard, suite 약 62초→24초 (PR #35).
- 9월 28일 telemetry heartbeat, 외부 cron으로 `report_telemetry_heartbeat.py --alert` 필요 (PR #36, 신규 테이블).
- 9월 28일 공통 `execute_query` 실행 코어, `rag_service.py` 10,511→9,863줄 (PR #37).
- 9월 28일 스트리밍 렌더링 프레임 단위 묶음·상태 전환만 스크린리더 안내 (PR #39).
- 9월 28일 `retrieval_mode`/`degraded_datasets` 응답·query log 노출, `/ready` heartbeat·복구 힌트 (PR #40).
- 9월 28일 검색 제한 상태 메인 서버 저장·프런트 표시 (PR #42, **DB migration 포함**).
- 9월 28일 SQL이 근거 문서를 확정한 경우 근거 선택 LLM 우회, 우회 사유 stage metadata 기록 (PR #44).
  질의 분석 우회 판단은 기존과 동일(190문항 53→53). 실제 selector 호출률은 미측정.
- 9월 28일 소급 수집 보조 도구와 공지 증분 revision 수정 (PR #45, 운영 미배포). 수집 기록 (PR #46).
- 9월 28일 main CI 불안정 테스트 수정: hybrid float 비교, relay timeout, proxy 누수, 프런트 fake clock (PR #47).
- 9월 28일 재색인 시 변경되지 않은 청크 벡터 재사용(임베딩 입력·모델 해시, Chroma 메타데이터) (PR #48).
  실제 artifact 재색인 시간 절감은 미측정.
- 9월 28일 데이터셋 검색 제한 동시성·단계별 지연 백분위 보고 스크립트 (PR #49).
- 9월 29일 staged·공지 재구축이 live와 같은 `retrieval_text`를 임베딩 (PR #50, 재색인 후 반영).

## Agent Tasks

아래는 역할별 작업 계획이며 실제 병렬 에이전트 실행 상태를 뜻하지 않는다.
실제로 할당·실행 중인 task는 위 Task Board에만 기록한다.

### RAG Agent

Current:
- 공지 증분 동기화·관리자 upsert 임베딩 입력 통일 PR #51 검토 대기.

Next:
- 공지 수정 코드 배포 후 자동 갱신과 strict gate 재검증.
- 데이터가 바뀌어 재색인할 때(MPS 사용) #17·#26·#27·#29·#50·#51이 함께 반영되므로, 그때 전후 retrieval을 비교한다.
  코드 변경만을 위한 재색인은 하지 않는다(human 결정, 9월 29일).
- 재구축 경로의 "enrich → 텍스트 제거 → 해시" 중복 블록 공통 helper화(선택).

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
- 재색인 방침(9월 29일 human 결정): 데이터가 바뀌지 않으면 재색인하지 않고, 할 때는 MPS로 한다.
  청크 표현·임베딩 입력 변경(#17·#26·#27·#29·#50·#51)은 다음 데이터 변경 재색인 때 반영된다.
  전후 비교용 읽기 전용 데이터 사본은 human이 제공한다(개인정보 포함 DB라 agent가 임의로 복사하지 않음).
- 운영 DB migration 적용 승인(백업 선행): #31(verification_status), #32(scheduler lease 테이블), #36(heartbeat 테이블), #42(retrieval_mode).
- 모든 replica 중단 감지용 외부 cron(`report_telemetry_heartbeat.py --alert`) 구성.
- GitHub `main` branch protection(직접 push·force push 차단, PR·CI 필수) 적용 여부.
  원격 저장소 설정 변경이므로 agent가 수행하지 않는다.
- 골든 평가 threshold(현재 0.75/0.80/0.70/0.80, 감사 권고 0.85) 상향 여부.
- 9월 28일 공식 규정 전 범위 수집 결과의 폐지 추정·이름 변경 후보를 사람 검토.
- PR #19 삭제 공지 탐지 운영 `dry_run` 관찰 후 `enforce` 전환 여부.
- 교직원 승인 후보 #22의 추가·삭제·연락처 변경 검토.
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
