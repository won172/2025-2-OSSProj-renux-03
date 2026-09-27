# 08. 백엔드 중계·클라이언트 전달

## 목표와 필요성

좋은 RAG 결과도 전송 중 일부가 유실되거나 완료되지 않은 답변이 저장되면 제품 품질이 무너진다. 현재 SSE terminal contract는 강점이며 **유지해야 한다.** 다음 단계는 검증 상태와 degraded 상태를 사용자에게 정확히 표현하고, 체감 지연과 프런트 번들 크기를 줄이는 것이다.

주요 구현은 [ChatRequestApis.cs](../../../../src/RenuxServer/Apis/Chat/ChatRequestApis.cs), [useChatStream.ts](../../../../src/RenuxServer/wwwroot/frontend/src/hooks/useChatStream.ts), [백엔드 계약 테스트](../../../../src/RenuxServer/Tests/Program.cs), [프런트 테스트](../../../../src/RenuxServer/wwwroot/frontend/tests)에 있다.

## 현재 상태

- 백엔드는 RAG SSE를 중계하면서 `completion -> done` 순서, request ID, source lineage를 검증한다.
- 중복 completion, done 선행, terminal 이후 text, 잘못된 request ID, 불완전 EOF, 취소를 성공으로 저장하지 않는다.
- 인증 사용자와 guest 소유권을 구분하고, guest token은 서버가 발급한 보호 토큰을 사용한다.
- 프런트 hook은 네트워크 chunk 중간에서 줄이 잘려도 buffer를 유지하고 unmount/새 요청 시 reader를 취소한다.
- 추천 질문은 본 답변 완료 후 별도 `/followups`로 가져오는 경로가 있다.
- 백엔드 RAG stream timeout은 5분, followup은 2분으로 사용자 대기 한계보다 길다.
- 프런트는 `grounded: boolean` 중심이라 `검증 실패`와 `검증기 불가`를 표현할 수 없다.
- RAG가 검증 전에 text를 보낼 경우 클라이언트는 이를 즉시 표시한다.

로컬 검증에서 프런트 50개 테스트, lint, build와 백엔드 계약 실행이 통과했다. 빌드 결과의 큰 항목은 main JS 약 238KB(gzip 76.5KB), Home chunk 약 219KB(gzip 68KB), CSS 약 332KB(gzip 49KB), 로고 약 592KB, 폰트 약 777KB였고 PWA precache는 약 2.6MB였다.

## 기능 개선 작업

### P0

- completion 계약을 `verification_status` 네 상태로 확장하고, 백엔드와 프런트가 값을 손실 없이 전달한다.
- 사용자에게 fallback, sparse-degraded, stale-data, verification-unavailable을 서로 다른 상태로 보여준다.
- 고위험 route의 provisional text는 grounding 완료 전 렌더링하지 않거나 명확한 임시 상태 영역에만 표시한다.
- CI에서 프런트 단위 테스트와 백엔드 계약 실행을 필수로 한다.

### P1

- 출처 카드에 문서 제목, 공식 도메인, 게시/적용일, 관련 claim을 표시한다.
- 출처가 여러 개인 답변은 문장/항목별 source anchor로 연결하고, 존재하지 않는 source ref를 렌더링하지 않는다.
- 중단/timeout 시 “다시 시도”가 같은 assistant 슬롯을 재사용하고 중복 사용자 메시지를 만들지 않게 한다.
- followup 요청은 본 답변의 request ID와 source refs를 요구하고, 화면 이탈 시 취소한다.
- 5분 global timeout 대신 연결, 첫 byte, 무활동, 전체 완료 timeout을 나눈다. 사용자에게 남은 상태를 표시한다.
- 인증/guest 모두에서 새로고침, 재연결, 네트워크 전환, 중복 제출 시나리오를 E2E로 검증한다.

### P2

- direct typed payload를 일정 카드, 식단 카드, 연락처 카드로 렌더링하고 텍스트 fallback도 유지한다.
- 접근성 기준에 맞춰 streaming 상태를 과도하게 반복 낭독하지 않고 완료/오류 상태를 live region으로 알린다.
- source click, retry, abort, feedback reason을 raw 질문 없이 계측한다.

## 성능 개선 작업

- 로고를 적절한 WebP/AVIF와 크기로 제공하고 592KB 원본을 초기 경로에서 제외한다.
- 777KB 폰트를 subset/woff2로 변환하고 필요한 weight만 preload한다.
- Home chunk의 Markdown/화면 기능을 lazy loading하고 route별 bundle budget을 둔다.
- PWA precache에서 관리자 전용 chunk와 큰 비필수 asset을 제외해 첫 설치 비용을 줄인다.
- token마다 전체 Markdown을 다시 렌더링하지 않도록 30~50ms 단위 batch 또는 requestAnimationFrame으로 갱신한다.
- TTFB, first meaningful text, completion, grounding, source render 시간을 브라우저에서 별도 측정한다.

## 권장 UX 상태 모델

| 상태 | 사용자 표현 | 저장 여부 |
|---|---|---|
| connecting/searching | 검색 중 표시 | 저장 안 함 |
| provisional | 저위험 답변 작성 중 | 완료 전 저장 안 함 |
| verified | 답변과 출처 표시 | 저장 |
| verification_failed | 안전한 대체문과 출처만 표시 | 원 생성문 저장 안 함 |
| verification_unavailable | 검증 불가 표기, 정책에 따른 제한 응답 | 정책별 |
| fallback/degraded | 이유와 재시도/공식 링크 제공 | terminal 계약 성공 시 저장 |
| cancelled/incomplete | 중단 상태와 재시도 | 완료 답변으로 저장 안 함 |

## 완료 조건

- 모든 terminal contract 위반이 저장 0건으로 유지된다.
- `passed/failed/unavailable/not_required`가 RAG → C# → React → 저장 DB에서 동일하다.
- 모바일 네트워크에서 TTFB p75 2초 이하, 직접 응답 p75 800ms 이하를 목표로 한다.
- 초기 전송 JS/CSS와 PWA precache가 설정한 bundle budget을 통과한다.
- 키보드, 스크린리더, 재연결, 취소, 중복 요청 E2E가 CI에서 통과한다.
