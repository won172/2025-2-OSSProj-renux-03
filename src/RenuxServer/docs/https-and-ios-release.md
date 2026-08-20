# 동똑이 HTTPS 및 iOS 배포 런북

## 1. 현재 구조와 주소의 역할

- 웹 주소: 사용자가 Safari/PWA로 접속하는 주소. 현재 Vercel 프런트가 이 역할을 한다.
- API 주소: iOS 앱과 웹 프런트가 로그인, 채팅, RAG, 알림을 요청하는 주소.
- `VITE_API_BASE_URL`: 프런트 빌드 시 API 주소를 번들에 넣는다. 공개 주소만 넣고 API 키나 JWT 키는 넣지 않는다.
- `DONGTTOK_API_HOST`: iOS `WKAppBoundDomains`에 넣는 호스트명이다. 스킴이나 경로 없이 `api.example.com` 형태만 사용한다.

App Store 빌드에는 로컬 IP, `http://`, 무료 임시 ngrok 주소를 사용하지 않는다. 심사 중에도 API와 데모 계정이 계속 동작해야 한다.

## 2. 안정적인 HTTPS 주소를 얻는 방법

현재 Docker/RAG 구조에는 영구 볼륨과 장시간 실행 프로세스가 필요하므로, 권장 구성은 `도메인 + 상시 실행 서버(VPS/VM 또는 영구 볼륨 지원 컨테이너 호스팅)`이다.

1. 소유 도메인을 준비하고 DNS에서 `api.<도메인>`을 서버 공인 IP로 연결한다.
2. 서버에 이 저장소와 Docker를 배치하고 `src/RenuxServer/docker-compose.yml`을 기준으로 서비스를 기동한다.
3. 호스트의 Caddy, Nginx 또는 호스팅 플랫폼의 TLS 기능으로 443 요청을 Compose의 Nginx 8080 포트에 전달한다.
4. PostgreSQL, Redis, Chroma/RAG 데이터, ASP.NET Data Protection 키에 영구 볼륨과 백업을 설정한다.
5. 서버 방화벽은 80/443만 공개하고 DB, Redis, RAG 내부 포트는 외부에 공개하지 않는다.
6. 다음을 외부 네트워크에서 확인한다.

```bash
curl -fsS https://api.<도메인>/health
curl -fsS https://api.<도메인>/ready
curl -i -X OPTIONS https://api.<도메인>/home/briefing \
  -H 'Origin: capacitor://localhost' \
  -H 'Access-Control-Request-Method: GET'
```

DNS와 TLS를 직접 관리하기 어렵다면 고정 커스텀 도메인을 제공하는 터널을 쓸 수 있다. 단, 터널 뒤의 컴퓨터가 항상 켜져 있어야 하므로 App Store 운영용으로는 상시 서버에 터널 에이전트를 두어야 한다. 무료 임시 터널 URL은 심사/운영 주소로 사용하지 않는다.

## 3. 서버 환경 연동

운영 `.env`의 실제 값은 Git에 커밋하지 않는다. 최소 설정은 다음과 같다.

```env
CORS_ALLOWED_ORIGINS=https://<웹-도메인>,capacitor://localhost
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=None
GUEST_COOKIE_SECURE=true
GUEST_COOKIE_SAMESITE=None
```

`capacitor://localhost`는 iOS 앱에 포함된 웹 화면의 기본 origin이다. ASP.NET은 현재 명시적 allowlist만 허용하며 쿠키 요청에는 `Access-Control-Allow-Credentials: true`를 반환한다.

## 4. iOS 번들 생성

```bash
cd src/RenuxServer/wwwroot/frontend
VITE_API_BASE_URL=https://api.<도메인> npm run ios:sync
```

릴리스 사전 검사는 저장소 루트에서 실행한다.

```bash
DONGTTOK_API_URL=https://api.<도메인> bash scripts/ios-release-preflight.sh
```

Xcode의 App target에서 다음을 설정한다.

- Team: Apple Developer Program에 가입된 개인 또는 조직
- Bundle Identifier: `com.wonmac.dongttok`을 실제 소유 식별자로 최종 확인
- Release 사용자 정의 빌드 설정 `DONGTTOK_API_HOST`: `api.<도메인>`
- Version / Build: 첫 제출은 `1.0` / `1`
- Signing: Automatically manage signing
- Device / orientation: 현재 검증 범위에 맞춰 iPhone 전용·세로 화면으로 제한되어 있다. iPad를 지원하려면 별도 레이아웃과 스크린샷 검증 후 target family를 확장한다.

그 후 실제 iPhone에서 로그인, 게스트 채팅, SSE 답변 완료, 앱 재실행 후 로그인 유지, 외부 링크, 공유 시트, 회원 탈퇴를 검증한다. Simulator 성공만으로 쿠키와 실제 네트워크 동작을 확정하지 않는다.

## 5. App Store 심사 제출 체크리스트

- App Store Connect 앱 레코드를 먼저 생성한다.
- 개인정보처리방침 URL과 지원 URL은 로그인 없이 열리는 공개 HTTPS 주소여야 한다.
- 심사용 일반학생 계정을 제공하고, 백엔드는 심사 기간 내내 켜 둔다.
- 앱 내 설정에서 회원 탈퇴를 완료할 수 있어야 한다.
- App Privacy에는 이름, 사용자 ID, 대화 내용, 학과, 진단/이용 데이터의 실제 수집·연동 여부를 코드와 개인정보처리방침에 맞게 답한다.
- OpenAI로 질문·학과·근거 발췌본을 전송하는 국외 이전 설명을 유지한다.
- `ITSAppUsesNonExemptEncryption=false`는 앱이 자체 비표준 암호화를 제공하지 않고 HTTPS 같은 면제 암호화만 사용한다는 전제다.
- 앱 이름, DGU 표기, 마스코트, 학교 데이터와 로고에 대한 사용 권한 증빙을 준비한다. 요청받으면 App Review에 제시한다.
- 스크린샷은 실제 앱 화면을 사용하고 로그인 화면만 제출하지 않는다.
- 결제 기능이 없으므로 인앱결제를 설정하지 않는다.
- 현재 추가한 네이티브 공유 시트와 답변 완료 햅틱을 Review Notes에 설명해 단순 웹 래퍼가 아님을 명확히 한다.

심사 통과를 보장할 수는 없지만, 계정 삭제·최소 기능성·개인정보·항상 켜진 백엔드·콘텐츠 권리·데모 접근이 이 프로젝트의 주요 사전 차단 항목이다.
