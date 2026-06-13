# 서버 실행 방법
RenuxServer 디렉토리에 `.env` 파일을 생성한다.

`.env` 파일 안에 다음과 같은 환경 변수를 설정한다.

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=[패스워드 입력]
POSTGRES_DB=[db 입력]

# dotnet npgsql postgres ConnectionString
CONNECTIONSTRING="Host=db; Port=5432; Database=[db입력(위와 동일)]; Username=postgres;Password=[패스워드 입력(위와 동일)]"
JWT_kEY=[키 입력]

# 질의분석/라우터는 항상 OpenAI를 사용하므로 키는 필수.
OPENAI_API_KEY=[openai api 키 입력]

# 라우터/질의분석용 모델(항상 OpenAI). 답변 생성 모델과는 별개의 변수다.
OPENAI_MODEL=gpt-4o-mini           # route_query / query_analysis 에서 사용

# 답변 생성 프로바이더: openai(기본) 또는 ollama
LLM_PROVIDER=openai
OPENAI_CHAT_MODEL=gpt-4o-mini      # 답변 생성용. LLM_PROVIDER=openai 일 때 사용 (OPENAI_MODEL과 다름)

# 아래 Ollama 설정은 LLM_PROVIDER=ollama 일 때만 필요
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_CHAT_MODEL=qwen3:4b-instruct-2507-q4_K_M

# 임베딩 디바이스: GPU 환경이면 cuda 로 설정해 속도 향상 (기본 cpu)
EMBED_DEVICE=cpu

# 임베딩 모델 (기본 nlpai-lab/KURE-v1 — 무료(MIT), 한국어 검색 벤치마크 최상위).
# 더 가벼운 무료 대안 (⚠️ 교체 시 python scripts/build_indices.py 전체 재인덱싱 필수):
#   EMBED_MODEL=intfloat/multilingual-e5-small      # 118M, ~5배 빠름/가벼움. 아래 프리픽스 필수
#   EMBED_QUERY_PREFIX="query: "
#   EMBED_PASSAGE_PREFIX="passage: "
#   EMBED_MODEL=Alibaba-NLP/gte-multilingual-base   # 305M, 프리픽스 불필요 (⚠️ 아래 trust_remote_code 필요)
# EMBED_MODEL=nlpai-lab/KURE-v1
# 운영에서는 모델 리비전(커밋 해시)을 고정해 공급망 무결성을 확보할 것:
# EMBED_MODEL_REVISION=<huggingface_commit_hash>
# RERANKER_MODEL_REVISION=<huggingface_commit_hash>

# ⚠️ 보안: 기본 모델(KURE-v1 / bge-reranker-v2-m3)은 표준 구조라 아래 플래그가 필요 없다.
# gte-multilingual-base처럼 저장소에 커스텀 파이썬 코드를 동봉한 모델만 trust_remote_code가
# 필요한데, 이는 모델 로드 중 임의 코드를 실행한다(공급망 실행 위험). 신뢰·검증된 모델을
# 고정 리비전으로만 쓸 때 한해서 켤 것. 미승인 모델을 운영에 쓰지 말 것. 기본 비활성.
# MODEL_TRUST_REMOTE_CODE=1

# Cross-encoder 리랭커 (선택): 하이브리드 top-20을 질의-문서 쌍으로 정밀 재정렬해
# 정확도를 높인다. 모델(~2GB) 무료, CPU에서 쿼리당 1~5초 지연. 기본 꺼짐.
# RERANKER_ENABLED=1
# RERANKER_MODEL=BAAI/bge-reranker-v2-m3
# RERANKER_CANDIDATES=20

# TF-IDF 아티팩트(*.pkl) 무결성 검증: 학습 시 기록한 sha256(artifacts/vectorizers/manifest.json)과
# 로드 직전 해시를 대조해 변조/손상된 pkl의 joblib 로드를 차단(fail-closed). 기본 활성.
# 매니페스트는 scripts/build_indices.py 재인덱싱 시 자동 갱신된다.
# TFIDF_VERIFY_INTEGRITY=1          # 0으로 끄면 검증 우회(운영 비권장)
# TFIDF_REQUIRE_MANIFEST=1          # 매니페스트 미등록 아티팩트도 거부(엄격 모드, 운영 권장)

# Parent-document 확장 (기본 켜짐): 생성 컨텍스트에 검색 청크의 앞뒤 이웃 청크를
# 함께 제공. 끄려면 PARENT_CONTEXT_ENABLED=0

# redis ConnectionString. 로컬 직접 실행 시 기본 포트는 6379(코드 기본값은 6380),
# docker compose 환경에서는 redis://redis:6379/0 을 사용한다.
REDIS_URL=redis://localhost:6379/0
```

> 답변 생성 모델을 로컬 LLM으로 바꾸려면 `LLM_PROVIDER=ollama` 로 설정하고
> 호스트에 Ollama가 실행 중이어야 합니다(`ollama pull <모델명>`). 한쪽 프로바이더가
> 실패하면 자동으로 반대 프로바이더로 폴백합니다(`LLM_FALLBACK_ENABLED=1`, 기본 활성).

`docker compose up --build` 명령어로 docker compose를 빌드 및 실행한다.

`localhost:8080`으로 접속해본다.
