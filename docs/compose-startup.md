# Docker Compose 시작

기존 `.env`, DB·검색 아티팩트, 모델 캐시가 준비된 checkout에서 다음 명령을 실행한다.
`-build`가 아니라 하이픈 두 개의 `--build`다.

```sh
cd src/RenuxServer
docker compose up --build
```

터미널을 닫은 뒤에도 실행하려면 `docker compose up -d --build`를 사용한다.
Docker Desktop/엔진이 먼저 실행되어 있어야 한다. 서비스는 `unless-stopped` 정책으로
Docker 엔진 재시작 후 다시 시작한다. 직접 중지한 컨테이너는 명시적으로 다시 시작해야 한다.
의존성의 health 대기는 Compose 실행 시 적용되며, 엔진 재시작의 순서를 보장하지는 않는다.

Compose는 Redis가 healthy가 된 뒤 RAG를 시작한다. PostgreSQL healthcheck와 RAG의
`/ready`가 성공하면 API를 시작한다. API `/ready`의 DB 연결 확인이 성공하면 nginx가 시작된다.
RAG `/ready`는 임베딩·필수 데이터·정본/검색 인덱스 lineage 검사도 포함한다.
준비 실패를 `/health` 성공으로 바꾸어 통과시키지 않는다. 임베딩 cold start 때문에
RAG 기동에는 수 분이 걸릴 수 있다. nginx의 로컬 주소는 `http://localhost:8080`이다.

## 최초 준비와 설정

- `src/RenuxServer/.env`와 `src/RAG/.env`가 필요하다. 각 `.env.example`을 참고해
  소유자가 실제 값을 준비한다. DB 사용자·암호·DB 이름·`CONNECTIONSTRING`, JWT와
  OpenAI 설정이 유효해야 한다. 다른 checkout의 secret 파일을 복사하거나 출력하지 않는다.
- `src/RAG/rag_database.db`는 디렉터리가 아닌 기존 SQLite 파일이어야 한다.
  파일이 없으면 Compose bind mount를 실패시키며 빈 디렉터리를 대신 만들지 않는다.
  `artifacts/`에는 서로 일치하는 정본 기반 Parquet·Chroma·lexical 인덱스가 필요하다.
  이 명령은 누락된 대학 데이터를 수집하거나 인덱스를 자동 복구하지 않는다.
- `src/RAG/huggingface_cache/`에 설정된 임베딩 모델과, 활성화했다면 reranker 모델의
  완전한 Hugging Face 캐시가 필요하다. Compose는 오프라인 모델 로드를 사용한다.
  모델을 바꾸면 기존 벡터와 호환되지 않으므로 별도 검증된 재색인이 필요하다.
- 기본 답변 생성은 OpenAI다. `LLM_PROVIDER=ollama`로 바꾸면 호스트의 Ollama도
  별도로 실행되어야 하며, 질의 분석·근거 검증 등에 쓰는 OpenAI 설정은 여전히 필요하다.
- 자동 수집 실행 여부는 기존 `RAG_SCHEDULER_ENABLED` 등 운영 설정을 따른다.
  기동 작업을 이유로 운영 스케줄러를 새로 활성화하지 않는다.

Compose `environment`에서 `${...}`로 선언한 `RAG_SCHEDULER_ENABLED`,
`LLM_PROVIDER` 등의 값은 shell 또는 `src/RenuxServer/.env`에서 설정한다.
이 항목은 `src/RAG/.env`에만 값을 넣어도 Compose의 명시 설정을 덮어쓰지 못한다.
`RAG_COLLECTION_POINTER_FILE`은 `env_file`로 전달하므로 `src/RAG/.env`에서 설정할 수 있다.

## 공지 인덱스 포인터

RAG entrypoint는 서비스 사용자 권한으로 포인터를 고른 뒤 해당 프로세스의
`RAG_COLLECTION_POINTER_FILE`에만 전달한다. JSON이나 DB·인덱스 파일을 고치지 않는다.

1. `RAG_COLLECTION_POINTER_FILE`에 명시한 비어 있지 않은 경로가 최우선이다.
   파일·스키마·대상 컬렉션이 잘못되면 기동 오류를 표시하며 다른 포인터로 바꾸지 않는다.
   Compose에서는 `src/RAG/.env`에 컨테이너 내부 경로를 지정할 수 있다.
2. 기본 `artifacts/collection_pointers.json`의 대상이 존재하면 기본 포인터를 사용한다.
3. 기본 대상이 없고 `artifacts/db_chroma/collection_pointers_rebuilt.json`이 존재하면,
   기본 논리 컬렉션을 모두 포함하고 모든 대상이 Chroma 기본 tenant/database에 존재할 때만
   복구 포인터를 사용한다. 기본 파일이 없는 경우에도 유효한 복구 포인터를 사용할 수 있다.
4. 복구 파일이 없는 새 설치는 기존 기동·readiness 동작을 유지한다.
   선택기는 새 데이터를 만들거나 bootstrap하지 않는다.

선택 과정은 Chroma 클라이언트를 생성하지 않고 SQLite catalog를 읽기 전용 immutable
연결로 확인한다. 비어 있지 않은 WAL/rollback journal이 있으면 오래된 catalog로
선택하지 않고 오류를 표시한다. 이 경우 운영 소유자가 별도 점검해야 한다.
대상 이름의 존재만 확인하므로 최종 내용·revision 일치는 기존 strict lineage 검사가
계속 판정한다. lineage 불일치는 `/ready` 503과 의존 서비스의 대기 상태로 남는다.

## 확인

```sh
docker compose ps
docker compose logs --tail=100 rag-service api
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://localhost:8080/ready
```

`RAG startup: using recovered collection pointer ...`는 선택한 경로의 로그다.
전체 데이터의 최신성이나 실제 질문 답변 품질을 증명하지 않는다. 로그를 공유할 때는
키·사용자 데이터가 없는지 확인한다. 잘못된 명시 포인터는 `.env`의 경로를 소유자가
바로잡아야 한다. 데이터·포인터 삭제나 재색인으로 기동 문제를 우회하지 않는다.

이 변경은 task branch에서 검증한 기동 코드다. primary checkout에 반영하는 main
병합과 운영 컨테이너 재생성·배포는 human 승인 단계다. fixture/설정 검사 통과를
실제 운영 전체 스택 기동 완료로 보고하지 않는다.
