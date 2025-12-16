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

OPENAI_API_KEY=[openai api 키 입력]
REDIS_URL=redis://localhost:3679/0 # redis ConnectionString
```

`docker compose up --build` 명령어로 docker compose를 빌드 및 실행한다.

`localhost:8080`으로 접속해본다.
