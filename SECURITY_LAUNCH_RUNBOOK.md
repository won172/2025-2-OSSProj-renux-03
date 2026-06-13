# 출시 보안 운영 Runbook

이 문서는 계정 접근, 실제 secret 값, 법무 승인 때문에 저장소 안에서 완전히 자동화할 수 없는 출시 차단급 운영 작업을 정리합니다.

## 1. 키 로테이션 및 Secret 이관

로컬 `.env` 파일에 들어간 적 있는 키는 출시 전 유출된 것으로 간주하고 교체합니다.

1. 새 JWT 서명 키를 생성합니다.
   ```bash
   openssl rand -base64 64
   ```

2. 각 제공자 대시보드에서 키를 교체합니다.
   - OpenAI: 새 project key 생성 → Fly secret 갱신 → 기존 키 폐기
   - Hugging Face: 로컬에서 사용한 토큰이 있으면 새 토큰 생성 → 런타임 갱신 → 기존 토큰 폐기

3. Fly secrets에 값을 설정합니다. 아래 값은 어떤 파일에도 커밋하지 않습니다.
   ```bash
   fly secrets set -a dongttok-api \
     CONNECTIONSTRING='Host=...;Port=5432;Database=...;Username=...;Password=...' \
     JWT_KEY='base64-key-from-openssl' \
     CORS_ALLOWED_ORIGINS='https://your-frontend.example'

   fly secrets set -a rag-cinder-harborbird-7558 \
     OPENAI_API_KEY='sk-...' \
     REDIS_URL='redis://...'
   ```

4. 값을 출력하지 않고 secret 존재 여부만 확인합니다.
   ```bash
   fly secrets list -a dongttok-api
   fly secrets list -a rag-cinder-harborbird-7558
   ```

5. 기존 키 폐기 후 배포합니다.
   ```bash
   fly deploy -a dongttok-api ./src/RenuxServer
   fly deploy -a rag-cinder-harborbird-7558 ./src/RAG
   ```

RAG `fly.toml`에는 `RAG_REQUIRE_OPENAI_API_KEY=1`이 설정되어 있습니다. 따라서 Fly 배포에서 `OPENAI_API_KEY`가 빠지면 깨진 요청을 받기 전에 startup에서 실패해야 합니다.

## 2. Secret 유출 검사

매 릴리스 전 실행합니다.

```bash
bash scripts/scan-secrets.sh
git status --short
```

스캐너는 tracked file 기준으로 OpenAI, Hugging Face, Postgres URL, JWT secret 패턴을 검사합니다. 로컬 `.env` 파일은 개발 중 존재할 수 있지만 반드시 ignore 상태여야 합니다.

## 3. 데이터베이스 백업

출시 전 운영 백업 방식을 하나 확정합니다.

- 권장: 자동 백업과 point-in-time restore가 있는 managed Postgres
- 단기 허용: 스케줄된 `pg_dump` + 암호화된 외부 저장소

수동 백업:

```bash
DATABASE_URL='postgres://user:pass@host:5432/db' scripts/backup-postgres.sh
```

복구 리허설:

```bash
pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL" backups/postgres/dongttok-YYYYMMDDTHHMMSSZ.dump
```

출시 전 disposable database에 실제 복구 테스트를 수행합니다. 복구해본 적 없는 백업은 운영적으로 검증된 백업이 아닙니다.

## 4. 약관/개인정보 결정

법무 문안은 아직 출시 차단 항목입니다. 아래 내용을 확정하고 승인받아야 합니다.

- 이용약관 URL/본문
- 개인정보처리방침 URL/본문
- 동의 전 게스트 채팅 허용 여부
- 채팅 로그 보관 기간
- 사용자 삭제/내보내기 요청 지원 여부

문안 승인 후 구현합니다.

- 실제 가입/온보딩 플로우의 동의 체크박스
- 사용자 레코드의 동의 시각 및 정책 버전 저장
- 로그인/온보딩/푸터 또는 헤더의 문서 링크

placeholder 법무 문안으로 출시하지 않습니다.
