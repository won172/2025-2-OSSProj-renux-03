# React 전환 계획

## 1. 목표
- 기존 정적 HTML/JS 자산을 React 기반 SPA로 단계적으로 대체한다.
- `wwwroot` 폴더를 그대로 유지하되, React 소스와 빌드 산출물을 명확히 구분한다.
- ASP.NET Core 서버와의 API 연동을 유지하면서 개발 편의성과 배포 자동화를 높인다.

## 2. 적용 범위 및 기본 전략
- `wwwroot`는 프론트엔드 프로젝트 루트로 사용하고, React 도구 체인을 도입한다(Vite + React + TypeScript 권장).
- 기존 `css/`, `js/`, `.html` 파일은 React 컴포넌트와 페이지로 재구성하고 필요 시 `public/`으로 이동한다.
- 외부 라이브러리(`lib/`)는 NPM 패키지로 대체하거나, 번들링이 어려운 일부만 `public/`에 두고 사용한다.

## 3. 예상 폴더 구조
```
RenuxServer/
  wwwroot/
    package.json
    vite.config.ts
    tsconfig.json
    public/
      index.html
      favicon.ico
      assets/              # 정적 리소스(이미지, 폰트 등)
    src/
      main.tsx             # 엔트리 포인트
      App.tsx
      pages/
        Home/
        SignIn/
        SignUp/
      components/
        common/
        layout/
      hooks/
      styles/
      api/
        client.ts          # API 클라이언트(axios/fetch 래퍼)
        auth.ts            # 인증 관련 요청
      store/               # 상태 관리 (필요 시)
    dist/                  # `npm run build` 결과(자동 생성)
    README.md              # 프론트엔드 전용 문서
```

## 4. 단계별 진행 계획
1. **환경 세팅**
   - Node.js 버전 확인 후 `npm init vite@latest` 또는 수동으로 Vite 프로젝트 초기화.
   - TypeScript, ESLint, Prettier, Testing Library 등 개발 도구 설치 및 기본 설정.
   - 절대 경로 alias, 환경 변수(`.env`, `.env.development`) 구성.

2. **기존 자산 마이그레이션**
   - `public/index.html`에 공통 메타태그, 외부 스크립트 삽입 여부 확인.
   - 기존 `css/*.css` → 전역 스타일(`src/styles/global.css`) 또는 컴포넌트 스코프 스타일로 분할.
   - `js/*.js`의 DOM 조작 로직을 React 컴포넌트 상태/이벤트로 변환.
   - `lib/`에서 사용 중인 라이브러리는 NPM 패키지 설치(예: Bootstrap, Axios)로 대체.

3. **페이지/라우팅 구성**
   - React Router로 `Home`, `SignIn`, `SignUp` 등 주요 페이지 라우팅 구성.
   - 공통 레이아웃, 헤더/푸터, 모달 등 재사용 컴포넌트를 `components/`에 구현.
   - 기존 팝업 및 챗봇 UI 구조는 문서(`popup-architecture.md`)를 참고해 컴포넌트 트리 설계.

4. **상태 및 데이터 연동**
   - `src/api/client.ts`에서 Axios 인스턴스 생성, 공통 헤더 및 에러 인터셉터 정의.
   - 폼 인증 로직(`signin.js`, `signup.js`)을 React 훅으로 옮기고, Validation 라이브러리 선택.
   - 전역 상태가 필요하면 React Query, Zustand, Recoil 등 도입 검토.

5. **빌드 및 배포 파이프라인**
   - `package.json` 스크립트: `npm run dev`, `npm run build`, `npm run preview`, `npm run lint`, `npm run test`.
   - `.csproj`에 `npm install` + `npm run build`를 프리/포스트 빌드 이벤트로 등록하거나, CI에서 실행 후 `dist/` 결과를 `wwwroot` 루트로 복사.
   - 프로덕션 빌드 시 `dist` 내 파일을 `wwwroot` 루트에 복사(`dist/index.html`, `dist/assets/*`).
   - 불필요한 개발 파일(`src/`, 설정 파일 등`)은 서버 배포 패키지에서 제외.

6. **테스트 및 검증**
   - 컴포넌트 단위 테스트와 주요 플로우(로그인/회원가입) E2E 테스트 작성.
   - 브라우저 호환성, 접근성 점검.
   - 서버와 연동한 통합 테스트(개발/스테이징 환경)로 API 호환성을 확인.

## 5. 서버 통신 및 연동 전략
- **개발 환경**
  - Vite dev server(`npm run dev`)에서 `/api` 요청을 ASP.NET Core 백엔드로 프록시 설정.
  - 예: `vite.config.ts`에 `server.proxy = { '/api': 'https://localhost:5001' }` 추가.
  - CORS 문제 없이 기존 API 엔드포인트를 그대로 호출 가능.

- **프로덕션 환경**
  - `npm run build` 후 생성된 정적 파일을 ASP.NET Core의 `UseStaticFiles`로 제공.
  - API 호출은 동일한 도메인에서 `fetch('/api/...')` 또는 Axios 인스턴스를 통해 진행.
  - 인증/세션 쿠키 방식이라면 도메인이 동일하므로 별도 설정 없이 기존과 동일하게 동작.

- **실시간/추가 통신**
  - SignalR 등 웹소켓을 사용 중이라면 React에서 해당 클라이언트를 재활용하거나 공식 패키지(`@microsoft/signalr`)로 연동.
  - 응답 스키마 변경이 없다면 기존 서버 코드는 수정 없이 그대로 사용 가능.

## 6. 후속 과제 및 체크리스트
- [ ] Node.js, NPM, Vite 초기 설정 마무리
- [ ] TypeScript 전환 여부 확정 및 도입 시 타입 정의 작성
- [ ] 기존 화면별 요구사항 문서화(`memory.md`, `nextplan.md` 참고)
- [ ] API 스펙 명세서 최신화 및 React 프로젝트 공유
- [ ] CI/CD 파이프라인에 프론트엔드 빌드 단계 추가
- [ ] 마이그레이션 완료 후 기존 정적 파일 제거 여부 검토

---
본 계획에 따라 React로 전환해도 서버와의 통신은 기존 REST API/SignalR 구조를 그대로 활용할 수 있으며, 개발/배포 환경만 React 도구 체인에 맞게 조정하면 된다.

## 7. 실무 진행 단계
1. **환경 준비**
   - Node.js LTS 설치 및 `node -v`, `npm -v`로 버전 확인.
   - `wwwroot`에서 `npm install` 실행 전 git 브랜치 분리(`feature/react-migration` 등).

2. **Vite 프로젝트 초기화**
   - `npm create vite@latest . -- --template react-ts`로 기본 틀 생성.
   - 생성된 파일을 기준으로 `.gitignore`, `tsconfig.json`, `eslintrc` 등 필요 설정 확인.
   - Vite 기본 실행이 되는지 `npm run dev`로 동작 검증.

3. **기존 자산 정리**
   - `public/`로 옮길 정적 파일(이미지, 폰트, favicon 등) 분류.
   - `src/` 내에 `pages/`, `components/`, `api/`, `styles/` 디렉터리 생성.
   - Legacy JS/CSS 파일을 기능별로 나눠 React 컴포넌트 초안 작성.

4. **라우팅 및 레이아웃 구성**
   - `npm install react-router-dom` 후 `src/main.tsx`에 BrowserRouter 설정.
   - `src/pages`에 기존 화면을 대응하는 라우트 컴포넌트 작성.
   - 공통 헤더/푸터는 `components/layout/`에 배치하고 페이지에서 재사용.

5. **API 연동 및 상태 관리**
   - `npm install axios` 후 `src/api/client.ts`에서 Axios 인스턴스 생성.
   - 인증/로그인 로직을 훅(`hooks/useAuth.ts`)으로 추출하고 폼 상태는 React Hook Form 등 사용 검토.
   - 필요 시 React Query나 Zustand를 도입해 서버 상태/전역 상태를 관리.

6. **개발 서버 프록시 설정**
   - `vite.config.ts`에 `server.proxy = { '/api': 'https://localhost:5001' }` 추가.
   - 프록시가 정상 작동하는지 `npm run dev`에서 API 호출 테스트.

7. **정적 빌드 및 백엔드 연동**
   - `npm run build`로 `dist/` 산출물을 생성하고, ASP.NET Core가 `dist` 내용을 서빙하도록 확인.
   - `.csproj` 빌드 단계에 `npm install`과 `npm run build`를 추가하거나 CI 스크립트에 포함.

8. **테스트와 검증**
   - 주요 컴포넌트 테스트(`npm install vitest @testing-library/react`) 작성.
   - 브라우저 호환성, 접근성, 반응형 동작을 수동/자동으로 확인.
   - 스테이징 환경에서 실제 서버와 통합 테스트 후 회귀 QA 진행.

9. **배포 및 유지보수**
   - 배포 파이프라인에서 `dist`를 `wwwroot` 루트로 복사하도록 구성.
   - 사용되지 않는 기존 정적 파일 제거 계획 수립.
   - 전환 후 운영 중 오류/피드백을 수집해 후속 작업(backlog) 갱신.

## 8. 진행 상황 기록 (최초 작업)
- Node.js `v24.9.0`, npm `11.6.0` 버전을 확인해 환경이 최신 LTS 범위인지 점검했다.
- `git status`로 현재 브랜치(`simho`)와 변경 파일을 확인해 React 마이그레이션 작업 범위를 파악했다.
- `wwwroot` 하위에 `legacy/` 폴더를 만들고 기존 정적 페이지(`index.html`, `signin.html`, `signup.html`)를 이동시켜 새로운 SPA 진입점과 혼동되지 않도록 분리했다.
- `npm create vite@latest frontend -- --template react-ts` 명령으로 임시 하위 폴더에 React + TypeScript + Vite 템플릿을 생성했다.
- 생성된 템플릿 파일을 `rsync -a frontend/ ./`로 `wwwroot` 루트에 복사한 뒤, 임시 폴더 `frontend/`를 삭제해 구조를 정리했다.
- 루트 `package.json`과 `vite.config.ts`, `tsconfig*.json`, `eslint.config.js`, `src/`, `public/` 등 기본 Vite 프로젝트 자산이 추가되었는지 확인했다.
- `npm install`을 실행해 템플릿이 요구하는 237개 패키지를 설치했고, `package-lock.json`을 생성해 향후 배포/빌드 시 동일한 버전으로 재현 가능하도록 했다.
- 작업 후 `git status`를 다시 확인해 신규 파일과 legacy 파일 이동 내역이 모두 추적되는지 검증했다.

# react 구조로 바꿔도 서버와 문제 없는지
네, 구조가 React/Vite 중심으로 바뀌어도 팀원의 서버에서 기존처럼 바로 동작할 수 있어요. 다만 다음 조건을 맞춰야 합니다.

- 개발 환경에서 기존처럼 API를 쓰려면 npm install 후 npm run dev를 실행하면 됩니다. vite.config.ts에 /api 프록시만 설정해 두면 React
개발 서버 → ASP.NET Core 서버로 그대로 요청이 넘어가서 CORS 문제도 없어요.
- 배포/운영 환경에서는 npm run build로 만들어지는 dist/ 폴더를 ASP.NET Core의 정적 파일 루트로 복사(또는 빌드 스크립트에서 자동으로
반영)하면 됩니다. 정적 파일 제공은 기존과 동일하게 UseStaticFiles가 처리하고, API 엔드포인트는 그대로 /api/* 경로로 호출돼요.
- 새 index.html은 Vite 번들에 맞춰져 있으니, ASP.NET Core가 wwwroot/index.html을 SPA 엔트리로 제공하도록만 맞춰주면 됩니다. 기존 HTML
은 legacy/에 남겨뒀으니 필요할 때 참고하거나 임시 라우팅으로 보여줄 수도 있어요.

즉, Node 빌드 스텝만 추가하면 서버 측 코드는 그대로 쓰면서 React 구조로 전환할 수 있습니다.
## 9. 진행 상황 기록 (홈 페이지 React 전환 착수)
- 기존 `css/`, `js/`, `lib/`, `legacy/` 디렉터리 자산을 확인해 어떤 파일을 우선 React로 옮길지 파악했다.
- 레이아웃/스타일 마이그레이션을 위해 `src/styles/global.css`를 생성하고, `css/index.css` 내용을 옮겨와 SPA에서도 동일한 룩앤필을 유지하도록 했다.
- React 컴포넌트 구조를 시작하기 위해 `src/pages/home/HomePage.tsx`를 작성했고, 로그인 상태 체크(`/auth/name`), 학과 목록 `/req/orgs`, 최근 채팅 `/chat/active`, 새 채팅 생성 `/chat/new` 흐름을 `useEffect`와 상태 기반으로 재구현했다.
- Bootstrap 의존성을 NPM 패키지(`bootstrap`)로 전환하고, 모달 동작을 React 상태(`isModalOpen`)로 제어하도록 바꿔 향후 유지보수를 쉽게 했다.
- 기본 Vite 산출물(`App.css`, `index.css`, `assets/` 내 로고 이미지)을 정리하고, `App.tsx`/`main.tsx`를 새 페이지와 글로벌 스타일을 로드하도록 수정했다.
- `npm run build`로 타입 검사 및 프로덕션 빌드를 수행해 새 구조가 정상적으로 컴파일되는 것을 확인했다.

## 10. 진행 상황 기록 (라우팅 및 인증 화면 React 전환)
- `react-router-dom`을 도입해 `src/App.tsx`에서 `/`, `/auth/in`, `/auth/up` 라우트를 구성하고, 존재하지 않는 경로는 홈으로 리다이렉트하도록 했다.
- 홈 화면 버튼이 SPA 내에서 이동하도록 `useNavigate`를 적용하고, 향후 추가될 라우트와의 일관성을 확보했다.
- 로그인 화면을 `src/pages/auth/SignInPage.tsx`로 이관해 폼 검증, `/auth/signin` API 호출, 오류 메시지 출력, 로그인 성공 시 홈으로 이동하는 흐름을 React 상태 기반으로 재구현했다.
- 회원가입 화면을 `src/pages/auth/SignUpPage.tsx`로 이전하여 전공/역할 데이터 로드(`/req/major`, `/req/role`), 아이디 중복 확인(`/auth/idcheck`), 비밀번호 일치 검증, 가입 요청(`/auth/signup`)을 모두 컴포넌트 상태로 관리하게 했다.
- 공통 인증 UI 스타일을 `src/styles/global.css`에 추가해 로그인/회원가입 화면이 기존 CSS 룩앤필을 유지하면서도 재사용 가능하게 정리했다.
- 변경 이후 `npm run build`를 실행해 타입 검사 및 프로덕션 빌드가 정상 완료되는지 확인했다.

## 11. 진행 상황 기록 (API 통신 정비)
- 개발 환경에서 백엔드 API를 손쉽게 호출할 수 있도록 `vite.config.ts`에 `/auth`, `/req`, `/chat` 경로를 ASP.NET Core(`https://localhost:5001`)로 넘겨주는 프록시를 추가했고, 필요 시 `.env.development`의 `VITE_DEV_SERVER_PROXY_TARGET` 값으로 대상 주소를 조정할 수 있게 했다.
- `src/api/client.ts`에 공통 `apiFetch` 유틸을 만들어 JSON 요청/응답, 에러 정보를 일관되게 다루게 했으며, 컴포넌트에서 중복된 fetch 로직을 제거했다.
- 홈/로그인/회원가입 화면(`HomePage`, `SignInPage`, `SignUpPage`)이 모두 `apiFetch`를 사용하도록 수정해 에러 메시지 관리와 상태 업데이트를 단순화했다.
- 레거시 `js/site.js`는 안내 주석뿐이라 추가 마이그레이션이 필요 없음을 확인했고, 관련 내용은 문서에 기록해 중복 작업을 피하도록 했다.
- `npm run build`로 타입 검사와 프로덕션 번들이 성공하는 것을 다시 확인해 새 구성에서 빌드 파이프라인이 유지됨을 검증했다.

## 12. 진행 상황 기록 (레이아웃/스타일 구조화)
- 홈 화면의 헤더와 사이드 패널을 `components/layout/AppHeader.tsx`, `components/chat/NewChatSection.tsx`, `components/chat/ActiveChatList.tsx`로 분리해 재사용성과 가독성을 높였다.
- 공통 타입을 `src/types/` 아래로 정리(`organization`, `chat`, `auth`, `user`)해 여러 컴포넌트가 동일한 도메인 모델을 참조하도록 했다.
- Bootstrap 기본 스타일 보완을 위해 `css/site.css` 내용을 `src/styles/bootstrap-overrides.css`로 옮기고, `main.tsx`에서 Bootstrap → overrides → global 순으로 로드하게 구성했다.
- 기존 `HomePage`, `SignInPage`, `SignUpPage`에 남아 있던 반복 fetch 로직과 마크업을 새 컴포넌트/유틸 기반으로 치환하면서 UI 동작은 유지하되 구조를 간결하게 유지했다.
- `npm run build`를 통해 타입 검사와 프로덕션 번들이 계속 성공하는지 확인하여 구조 개편 이후에도 빌드 안정성을 검증했다.

## 13. 자동 반영 구조 정비 (2025-02-15)
- `wwwroot/frontend/`를 Vite 프로젝트 전용 디렉터리로 분리했다. 개발용 `index.html`, `src/`, `public/`, `tsconfig*`, `package.json` 등은 모두 이 폴더 아래에서 관리하고, 서버가 제공할 정적 자산만 `wwwroot/` 루트에 남도록 했다.
- `npm run build` 스크립트에 `node scripts/sync-static.mjs`를 이어붙였다. 빌드가 끝나면 `frontend/dist` 안의 산출물을 검사한 뒤 기존 `wwwroot/index.html`, `wwwroot/assets/` 등 동명의 파일·폴더를 삭제하고 새 결과물을 덮어써서 서버 정적 자산이 자동으로 최신화된다. 필요할 경우 `npm run build:sync`로 동기화만 따로 수행 가능하다.
- `scripts/sync-static.mjs`는 dist가 비어 있을 때는 안전장치로 동작을 중단하고, 디렉터리/파일 타입을 구분해 복사한다. 추후 빌드 산출물에 새 파일이 생겨도 이름만 일치하면 자동으로 갱신된다.
- ASP.NET Core 진입점은 `MapFallbackToFile("/index.html")`로 교체해 React Router 라우트(`/auth/in`, `/auth/up` 등)를 직접 새로고침해도 항상 `wwwroot/index.html`을 내려주도록 했다. API 라우트는 기존 `app.AddAuthApis()` 등에서 선처리하므로 SPA 라우팅과 충돌하지 않는다.
- 배포 자동화: (1) `wwwroot/frontend`에서 `npm ci && npm run build`를 실행하면 정적 자산이 `wwwroot/`에 반영되고, (2) 이후 `dotnet publish` 또는 서버 재시작만 하면 된다. CI에서는 `npm run build`를 dotnet 빌드 이전 단계에 배치하면 추가 스크립트 없이 최신 프런트엔드가 실립니다.
- 남은 과제: 프런트엔드 CI/CD 파이프라인에 Node 캐시(`npm ci`)를 붙이고, 서버 빌드 또는 Docker 이미지 생성 시 `npm run build`를 자동으로 호출하도록 스크립트화한다.
- `legacy/` 하위에 기존 `css/`, `js/`, `lib/` 자산을 그대로 옮겨 보관했다. 덕분에 예전 HTML을 참고하거나 임시로 띄울 때 경로 깨짐 없이 동작하고, 루트 `wwwroot`에는 React 번들과 문서만 남는다.
