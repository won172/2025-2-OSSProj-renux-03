# Canonical lineage release gate

동똑이의 검색 데이터는 SQLite `SourceDocument`를 데이터셋별 정본으로 사용한다.
Parquet, FTS/BM25, Chroma는 모두 다시 만들 수 있는 파생물이며 독립 정본이 아니다.

## 필수 불변식

각 데이터셋(`notices`, `rules`, `schedule`, `courses`, `staff`, `meals`)은 다음을 모두
만족해야 한다.

1. `active`·`updated` SourceDocument의 `document_key` 집합과 Parquet의 고유 `doc_id`
   집합이 정확히 같다.
2. Parquet의 `chunk_id` 집합과 Chroma ID 집합이 정확히 같다.
3. 각 Chroma 청크의 `metadata.doc_id`가 같은 `chunk_id`의 Parquet 부모 `doc_id`와 같다.
4. 정본키와 청크키에 공백값·중복값이 없고, 세 계층 모두 비어 있지 않다.

검사 보고서는 집계값과 비율만 출력하며 제목, 본문, 질의, 사용자 정보, API 키를
포함하지 않는다.

## 수동 검사

Python 3.11 환경에서 실행한다.

```bash
HF_HUB_OFFLINE=1 .venv311/bin/python scripts/report_canonical_lineage.py --mode strict
```

일부 데이터셋만 확인하려면 다음과 같이 실행한다.

```bash
.venv311/bin/python scripts/report_canonical_lineage.py \
  --mode strict --datasets notices meals
```

정상일 때 종료 코드는 `0`, 하나라도 불일치하거나 검사할 수 없으면 `1`이다.

## 빌드 시점 게이트 (필수)

색인 build/rebuild 스크립트는 작업한 데이터셋에 대해 위 strict 검사를 자동 실행하고,
실패하면 종료 코드 `1`로 끝난다. 출력은 데이터셋·지표 이름·건수·오류 유형만 담는다.

| 스크립트 | 검사 시점 | 실패 시 상태 |
|---|---|---|
| `build_indices.py` | 게시 **후** (운영 Chroma/Parquet 제자리 갱신, 스테이징 없음). 실제 재색인된 데이터셋만(중간 실패한 데이터셋 포함) | "published but lineage failed" — 아래 롤백 절차 수행 |
| `rebuild_lexical_indices.py` | 재구축 **전** (희소 재구축은 SourceDocument·Parquet·Chroma를 바꾸지 않으므로 사전 검사와 사후 검사가 같다). `--state`/`--restore`는 검사하지 않음 | 아무것도 덮어쓰지 않음 |
| `rebuild_dense_indices_isolated.py build/verify` | 격리 경로 검증(`verified`) 직후, 격리 Chroma 컬렉션 기준 | 활성화 기능이 없으므로 게시 없음. 해당 스테이징 경로를 승격하지 않는다 |
| `rebuild_notices_dense.py build` | 스테이징 컬렉션 검증 직후, 체크포인트의 원본 artifact·물리 컬렉션 기준 | 포인터 변경 없음 |
| `rebuild_notices_dense.py activate` | 포인터 전환 **전** | 전환 거부(`activation_refused`), 포인터 그대로 |

비상 시에만 `--skip-lineage-gate`로 건너뛸 수 있다(`build_indices.py`,
`rebuild_lexical_indices.py`, isolated 스크립트의 `build`/`verify`, notices 스크립트의
`build`/`activate`). 건너뛰면 경고 배너가 stderr에 출력되고, JSON을 내는 스크립트는
`"lineage_gate": {"status": "skipped"}`를 기록한다. 이 경우 배포 전에 반드시 수동 strict
검사를 실행한다.

주의: notices `activate` 게이트는 `maintenance_lock` 밖에서 실행되며, 운영 설정 artifact가
아니라 체크포인트의 `source_artifact`와 비교한다. `--artifact`로 별도 파일을 지정해 빌드했다면
활성화 후 `scripts/report_canonical_lineage.py --mode strict`로 운영 기준 검사를 반드시 다시 실행한다.

### 게시 후 실패 시 롤백

- `build_indices.py`: 원인(정본 누락·중복, 청크 불일치)을 고친 뒤 같은 데이터셋을 다시
  재구축하거나, 사전에 떠둔 artifacts/Chroma 백업으로 복원한다. 복원 후 수동 strict 검사를
  다시 실행해 `0`을 확인한다.
- 희소 인덱스: `rebuild_lexical_indices.py --restore <backup-dir>`.
- notices dense 포인터: `rebuild_notices_dense.py rollback --confirm-active-collection <active>`.

## 서버·배포 동작

- 서버 시작과 런타임 재색인 후 동일 검사가 자동 실행된다.
- 불일치 시 `/ready`는 HTTP 503과 `canonical_lineage` 실패 정보를 반환한다.
- 실제 후보 골든 러너는 `/ready`가 통과하기 전에 LLM 요청을 보내지 않는다.
- 일반 CI는 검사 모듈과 실패 조건을 빈 DB·fixture 데이터로 검증한다. 실제 대용량
  SQLite/Chroma는 Git에 포함되지 않으므로 실데이터 게이트는 배포 후보에서 실행한다.

## 수집 경보

스케줄러의 `partial`·`failed`만 Slack 호환 Webhook으로 보낼 수 있다.

```dotenv
RAG_SCHEDULER_ALERT_WEBHOOK_URL=https://hooks.example/...
RAG_SCHEDULER_ALERT_TIMEOUT_SECONDS=5
```

Webhook 주소가 없으면 전송하지 않는다. 전송 실패는 수집 트랜잭션을 실패시키지 않고
구조화 로그에 경고만 남긴다. 알림 payload에는 작업명, 상태, 집계 메시지, 시각만 담긴다.

## 환경변수 우선순위

`src/config.py`는 `src/RAG/.env`를 명시적으로 읽되 `override=False`를 사용한다. 따라서
Docker Compose, CI, 시크릿 매니저가 프로세스 환경에 주입한 값이 항상 로컬 `.env`보다
우선한다. `.env`는 Git과 Docker build context에서 제외되어야 한다.
