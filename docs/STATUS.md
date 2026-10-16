# Dongttok Current Status

Last Updated: 2026-10-16 (RAG 503 운영 복구·일반 Compose 기동 검증)

이 문서는 현재 작업과 다음 실행 순서의 요약이다. 코드·로컬 검증 기준이며,
운영 서버의 실시간 상태나 배포 승인 여부를 대신하지 않는다.
설정 상태는 코드 기본값을 기준으로 기록한다.
아래 9월 운영·RAG 기록은 이번 UI 작업에서 다시 확인하지 않은 당시 기록이다.

## Current Goal

동똑이 RAG 검색 품질과 서비스 안정성을 개선한다.
질문에 맞게 구조화 SQL 관계 조회, 키워드 검색, 벡터 검색을 선택하고,
동국대학교 공식 출처와 정본 데이터에 근거한 답변을 유지한다.

RAG 503을 운영에서 복구했고, 재발 방지 PR #73을 human 승인 후 main에 병합했다.
기존 primary 경로에서 별도 override 없는 Compose 전체 기동과 외부 게스트 채팅을 확인했다.

10월 13일 화면 감사에서 확인한 모바일 입력·알림 설정·관리자 화면 문제를
우선순위에 따라 개선했다. main 병합, 운영 웹/API 및 TestFlight 1.0(8) 반영을 마쳤고,
iOS 26.5 시뮬레이터에서 테스트 계정 로그인 후 설정·관리자·실제 채팅 저장/복원 검증까지 완료했다.

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
