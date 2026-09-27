# Dongttok Agent Rules

이 문서는 이 저장소에서 작업하는 모든 AI coding agent(Claude, Codex 등)와
사람 기여자가 따르는 공통 규칙이다. Orchestrator 전용 절차는 [CLAUDE.md](CLAUDE.md)에 있다.

## Architecture

Frontend
React / Vite / TypeScript

Main Backend
ASP.NET Core

RAG Server
Python / FastAPI

Storage
PostgreSQL
ChromaDB
Redis

상세 구조는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), 현재 작업 상태는
[docs/STATUS.md](docs/STATUS.md)를 본다.

### Repository Layout

| 경로 | 내용 | 담당 역할 |
|---|---|---|
| `src/RenuxServer/wwwroot/frontend/` | React/Vite/TypeScript 웹 프런트, Capacitor iOS(`ios/`) | Client |
| `src/RenuxServer/wwwroot/` (frontend 제외) | 프런트 빌드 산출물(추적됨) | Client |
| `src/RenuxServer/` | ASP.NET Core 메인 서버, EF Migrations, nginx/compose 설정 | RAG/Backend |
| `src/RenuxServer/Tests/` | 메인 서버 계약 테스트(콘솔 실행형) | QA |
| `src/RAG/` | FastAPI RAG 서버, 수집·파싱·색인·검색·생성 | RAG/Backend |
| `src/RAG/tests/` | RAG pytest, 골든 매트릭스, qrels, replay 증거 | QA |
| `.github/workflows/` | `ci.yml`(secret scan, frontend, backend, RAG pytest), `rag-golden-release.yml` | Orchestrator |
| `scripts/` | `scan-secrets.sh`, 백업·iOS 릴리스 보조 스크립트 | Orchestrator |

## Core Principle

Every generated answer must be grounded in official
Dongguk University sources whenever the query requires
university-specific factual information.

Do not improve answer fluency at the expense of grounding.

## Development Rules

- Never modify main directly.
- Create a branch/worktree for every task.
- Run relevant tests before completion.
- Never commit secrets.
- Do not perform destructive DB operations.
- Do not deploy production without human approval.
- Preserve source attribution.
- Retrieval changes require regression testing.

## Agent Roles

| Role | 담당 범위 |
|---|---|
| **Orchestrator** | 상태 파악, 우선순위, 작업 분해, branch/worktree 생성·정리, agent 할당, 통합 확인, `docs/STATUS.md` 최종 갱신, human approval 요청. 모든 구현을 직접 하지 않는다. |
| **RAG / Backend Agent** | FastAPI, RAG pipeline, crawling, parsing, HWP/PDF 처리, retrieval, reranking, grounding, metadata, DB 관련 backend 로직, ASP.NET Core 서버 로직. |
| **Client Agent** | React, Vite, TypeScript, UI/UX, Capacitor, iOS client 코드. |
| **QA / Review Agent** | unit·integration·regression test, RAG evaluation, git diff review, 회귀·보안 위험 확인. 구현 agent의 결과와 보고를 그대로 신뢰하지 않고 직접 재실행·재확인한다. |

Agent는 할당받은 역할과 task 범위 밖의 파일을 수정하지 않는다. 범위 밖 변경이
필요하면 멈추고 Orchestrator에게 보고한다.

# Git Workflow

- main 직접 commit 금지. main에서 기능 개발을 하지 않는다.
- **Base branch는 `main`이다.** 모든 feature/fix/refactor/test/docs/chore branch와 worktree는
  작업 시작 직전 `git fetch origin`으로 확인한 최신 `origin/main`에서 만든다.
  PR 대상도 `main`이다.
- task마다 별도 branch 사용: **One task = One branch = One worktree**.
- task마다 별도 worktree 사용.
- 다른 agent의 worktree 수정 금지. 자신에게 할당된 worktree 밖의 파일을 쓰지 않는다.
- 같은 branch를 여러 worktree에서 동시에 사용하지 않는다(git도 이를 거부한다).
- force push main 금지.
- shared history rewrite 금지: push된 branch에 대한 rebase/amend 후 force push,
  `git reset --hard`로 남의 커밋 제거, `git push --delete` 등을 하지 않는다.
- main merge는 human owner가 승인할 때만 수행한다. Agent는 PR 또는 merge candidate 상태까지만 만든다.

## Branch Naming

`<prefix>/<작업-내용-kebab-case>` 형식. agent 이름·모델 이름·날짜를 넣지 않는다.

| Prefix | 용도 | 예 |
|---|---|---|
| `feat/` | 새로운 기능 | `feat/ontology-retrieval`, `feat/ios-login` |
| `fix/` | 버그 수정 | `fix/hwp-parser`, `fix/metadata-filter` |
| `refactor/` | 구조 개선 (동작 변경 없음) | `refactor/reranker` |
| `test/` | 테스트/evaluation | `test/rag-regression` |
| `docs/` | 문서 | `docs/architecture` |
| `chore/` | 설정/의존성, CI, 유지보수 | `chore/ci-cache` |

기존 `codex/*`, `auto/*`, `feature/*`, `redesign/*` branch는 이 규칙 이전에
만들어졌다. 이름을 바꾸거나 삭제하지 말고 그대로 둔다.

## Worktree Layout

worktree는 저장소 **상위 디렉터리**의 `dongttok-worktrees/` 아래에 만든다.
저장소 밖이므로 `.gitignore` 대상이 아니며 git 추적에 섞이지 않는다.

```text
dongttok                          main                 ← primary checkout (Orchestrator)
dongttok-worktrees/rag-retrieval  feat/rag-retrieval   ← task별 worktree
dongttok-worktrees/ios-auth       feat/ios-auth
dongttok-worktrees/rag-evaluation test/rag-evaluation
```

worktree 디렉터리 이름은 branch 이름에서 prefix를 뗀 slug다. primary checkout은
`main`을 유지하고 기능 개발에 쓰지 않는다.

worktree 생성·제거는 Orchestrator가 한다([CLAUDE.md](CLAUDE.md)의 Worktree Lifecycle).
Agent는 스스로 새 worktree를 만들거나 다른 worktree를 제거하지 않는다.

### Worktree 안의 로컬 데이터와 secret

새 worktree는 git이 추적하는 파일만 가진다. 다음은 **없다**:

- `.env` 계열 파일(secret). `.env.example`만 있다.
- `src/RAG/rag_database.db`(로컬 약 0.5GB), `src/RAG/artifacts/`의 Chroma·chunks 등
  대부분(추적되는 vectorizer pkl·manifest·pointer 제외), `huggingface_cache/`.
- `node_modules/`, Python venv, `bin/`·`obj/`.

규칙:

- 의존성은 worktree 안에서 새로 설치한다(`npm ci`, venv 생성 후 `pip install -r requirements.txt`).
- `.env`를 다른 checkout에서 복사하거나 내용을 출력·문서화하지 않는다. secret이 필요한
  작업이면 멈추고 Orchestrator에게 보고한다.
- 기본 테스트는 CI와 같이 로컬 데이터 없이 실행한다(아래 Test Commands).
- 실제 데이터가 필요한 평가는 primary checkout의 DB·artifacts를 **직접 쓰지 않는다**.
  Orchestrator가 승인한 읽기 전용 사본 경로를 `RAG_DATABASE_FILE` 등으로 지정해 사용한다.
- primary checkout(`dongttok/`)의 파일은 수정하지 않는다.

# Before Starting

Agent는 작업 시작 전에:

1. AGENTS.md 읽기
2. docs/ARCHITECTURE.md 읽기
3. docs/STATUS.md 읽기
4. 자신의 task 확인 (목표, 범위, 완료 조건, 담당 역할)
5. 자신의 branch 확인: `git branch --show-current`
6. 자신의 worktree 확인: `git rev-parse --show-toplevel`이 할당된 경로인지,
   `git status`가 깨끗한지
7. 관련 기존 코드 분석 (변경 대상, 호출부, 기존 테스트)

branch나 worktree가 할당 내용과 다르면 코드를 수정하지 말고 보고한다.

# During Work

- task 범위 안의 최소 변경을 한다. 관련 없는 리팩터링·포맷팅을 섞지 않는다.
- 기존 테스트를 약화·삭제·skip 처리해서 통과시키지 않는다. 필요하면 이유와 함께 보고한다.
- commit은 자신의 task branch에만 한다. push는 Orchestrator가 지시한 경우에만 한다.
- Production 서버 접속, production DB 조회·변경, 배포, secret 변경을 하지 않는다.
- 추측이 필요한 요구사항은 구현하지 말고 질문으로 남긴다.

# Before Completion

Agent는 작업 완료 전에:

1. 관련 test 실행 (아래 Test Commands, 변경 영역 전체)
2. git diff 검토: `git diff main...HEAD` 및 `git status`
3. secret 포함 여부 확인: `bash scripts/scan-secrets.sh`, `.env`·키·인증서·DB dump 미포함 확인
4. 예상하지 못한 파일 변경 확인 (빌드 산출물, lock 파일, 다른 영역 파일)
5. 변경 내용 정리
6. known risk 정리
7. remaining work 정리

완료 보고에는 반드시 다음을 포함한다.

```markdown
## Completion Report
- Task / Branch / Worktree:
- Files changed:
- Implementation summary:
- Tests executed: (정확한 명령)
- Test results: (통과/실패 수, 실패 시 출력 요약. 실행하지 못한 테스트는 이유와 함께 명시)
- Known risks:
- Remaining work:
```

"테스트를 실행하지 않았음"과 "테스트 통과"를 구분해서 보고한다.

## Test Commands

CI(`.github/workflows/ci.yml`)와 같은 명령이다. 저장소 루트 기준 경로다.

| 영역 | 명령 |
|---|---|
| Secret scan | `bash scripts/scan-secrets.sh` |
| Frontend | `cd src/RenuxServer/wwwroot/frontend && npm ci && npm run lint && npm test && npm run build` |
| Main Backend | `cd src/RenuxServer && dotnet restore RenuxServer.sln && dotnet build RenuxServer.sln --configuration Release --no-restore` |
| Backend 계약 테스트 | `cd src/RenuxServer && dotnet run --project Tests/RenuxServer.ContractTests.csproj --configuration Release` |
| RAG | `cd src/RAG && python -m pytest -q` |
| RAG (빈 DB) | `cd src/RAG && RAG_DATABASE_FILE="$(mktemp -d)/empty-rag.db" python -m pytest -q` |

주의:

- `npm run build`는 `scripts/sync-static.mjs`로 추적 중인 `src/RenuxServer/wwwroot/assets/` 등을
  다시 쓴다. 빌드 산출물 갱신이 task 목적이 아니면 해당 변경을 commit하지 않는다.
- `rag-golden-release.yml`은 `RAG_GOLDEN_BASE_URL`이 없으면 평가를 건너뛴다.
  CI 성공을 실제 후보 검증으로 보고하지 않는다.

## RAG Change Verification

동똑이는 공식 대학 자료 기반 RAG 서비스다. 검색/RAG 변경은 "코드가 실행된다"는
이유만으로 완료 처리하지 않는다. 가능한 범위에서 다음을 확인하고 보고한다.

- retrieval 결과 변화 (변경 전/후 같은 질문·기준일·revision 비교)
- source attribution (문서·청크 식별자, 원문 URL 보존)
- grounding (생성 후 근거성 검사 동작, `grounded=null`을 성공으로 취급하지 않음)
- no-answer behavior (근거 부족 시 추측하지 않음)
- metadata filtering (캠퍼스·학과·학번·학기·공개 범위)
- 최신성 처리 (`as_of`, 적용기간, 마감일, 수집 신선도)
- 기존 정상 질문 regression (`tests/golden_matrix.csv`, qrels)

확인하지 못한 항목은 "미확인"으로 명시한다. 공식 근거가 없는 답변을 더 자연스럽게
만드는 것보다 근거가 정확한 답변을 유지하는 것을 우선한다. 자동 라벨 평가와
사람 판정 평가를 구분한다.

# Human Approval Gates

다음은 어떤 agent도 자동으로 수행하지 않는다. 필요하면 작업을 멈추고 human owner에게
승인을 요청한다.

- main merge
- production deployment
- production DB destructive operation
- production DB migration
- production data 삭제
- secret 변경
- API key 변경
- production infrastructure 변경
- force push
- shared history rewrite

## docs/STATUS.md

`docs/STATUS.md`는 Orchestrator가 최종 갱신한다. 구현·QA agent는 STATUS.md를 직접
수정하지 않고 Completion Report로 결과를 전달한다. (동시 수정 충돌 방지)
