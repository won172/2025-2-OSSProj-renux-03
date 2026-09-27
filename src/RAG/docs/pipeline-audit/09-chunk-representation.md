# 09. 청크 표현(Representation) 품질

점검 기준일: 2026-09-21

## 목표와 필요성

이 계층은 정본(`SourceDocument`)을 **검색 가능한 텍스트**로 바꾸는 단계다. 임베딩 벡터와 BM25/FTS5 항, 그리고 LLM이 실제로 읽는 근거 문자열이 모두 여기서 결정된다.

[01. 수집·정본](01-ingestion-canonical.md)이 "정본과 파생물이 **같은 것**을 가리키는가"(정합성)를 다룬다면, 이 문서는 "그 파생물이 **검색에 쓸 만한 형태**인가"(표현 품질)를 다룬다. 두 문제는 독립적이다. 계보 검사를 100% 통과해도 청크 텍스트가 내부 필드로 채워져 있으면 검색은 그만큼 나빠진다.

주요 구현은 [preprocess.py](../../src/utils/preprocess.py), [ingest.py](../../src/pipelines/ingest.py)의 `build_*_chunks`, [retrieval_context.py](../../src/services/retrieval_context.py)에 있다.

## 파이프라인

```
크롤러 → SourceDocument(정본 JSON + content_hash)
       → 도메인 테이블(notices/rules/courses/staff/schedule)
       → build_*_chunks()          ← 데이터셋별 텍스트 투영
       → to_chunks() / chunk_text() ← 분할 + 제목 접두
       → chunk_text 컬럼
       → enrich_retrieval_fields()  ← 검색 전용 헤더 부착
       → retrieval_text 컬럼
       → Chroma(임베딩) + BM25/FTS5 + parquet + chunks 테이블
```

`chunk_text`는 LLM 프롬프트에, `retrieval_text`는 임베딩·희소 인덱스에 쓰인다. 하이브리드 검색 결과는 `chunk_text`를 반환한다([hybrid.py:965](../../src/search/hybrid.py)).

## 현재 상태

정본 15,939건 → 청크 33,662건.

| 데이터셋 | 청크 | 문서 | 청크/문서 | 본문 중앙값 | 보일러플레이트 | 120자 미만 | URL 파손 | 내부 필드 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| notices | 11,951 | 6,021 | 1.98 | 434자 | 38.7% | 8.0% | **36.8%** | 0% |
| courses | 8,726 | 4,395 | 1.99 | 400자 | 14.8% | 0.1% | **95.6%** | **77.4%** |
| rules | 8,570 | 624 | 13.73 | 593자 | 28.3% | 1.3% | 1.7% | 0% |
| staff | 4,303 | 4,303 | 1.00 | 150자 | **81.3%** | 15.3% | 0% | 0% |
| schedule | 102 | 102 | 1.00 | 93자 | **64.2%** | 89.2% | 0% | 0% |
| meals | 10 | 10 | 1.00 | 256자 | 41.1% | 0% | 0% | 0% |

- 보일러플레이트 = `retrieval_context` 헤더 + 중복된 제목이 `retrieval_text`에서 차지하는 비율.
- 내부 필드 = `collected_at:`, `collection_status:`, `data_quality_score:`, `course_code_conflict:`, `availability_status:` 등 파이프라인 bookkeeping이 본문에 섞인 청크 비율.

## 유지해야 할 설계

- **`chunk_text`와 `retrieval_text` 분리.** 검색용 문맥 헤더가 LLM 프롬프트를 오염시키지 않는다. courses만 예외인데, 그건 헤더가 아니라 본문 투영이 잘못된 경우다.
- **`_persist_replacing_collection`의 고아 정리 순서.** upsert → 검증 → stale 삭제라서 재구축 중 컬렉션이 비지 않는다([ingest.py:326](../../src/pipelines/ingest.py)).
- **meals의 (날짜×식당) 1레코드 = 1청크.** 구조화 레코드를 쪼개지 않는 올바른 패턴이며, 다른 구조화 데이터셋이 따라야 할 기준이다.
- **정본 `content_hash`가 `collected_at`/`db_id`를 제외**하는 것([ingest.py:448](../../src/pipelines/ingest.py)). 다만 청크 텍스트에는 이 원칙이 적용되지 않았다(P0-2 참조).

## 문제 진단

### P0-1. 청킹 **전에** 문서 구조가 파괴된다

[`normalize_whitespace()`](../../src/utils/preprocess.py)가 `\n\n` → `\n`, 단일 `\n` → 공백으로 접는다. 그런데 [`chunk_text()`](../../src/utils/preprocess.py)가 이 함수를 한 번 더 호출한 뒤 `RecursiveCharacterTextSplitter(separators=["\n\n", "\n", ". ", ...])`로 분할한다. **첫 번째 separator `"\n\n"`는 어떤 입력에서도 매치되지 않는다.** 실제 분할 단위는 문단이 아니라, 같은 함수가 뒤늦게 재삽입한 "문장"이다.

같은 함수의 구두점 정규화(`preprocess.py:92`)가 URL과 번호 목록을 부순다.

```
IN : 제2조(정의)용어는 다음과 같다.\n1. 학생\n2. 교원\n\n신청: https://www.dongguk.edu/apply?id=3
OUT: 제2조(정의)용어는 다음과 같다.\n1.\n학생 2.\n교원\n신청: https: / / www. dongguk. edu/ apply? id=3
```

영향:

- 공지 청크 36.8%, 과목 청크 95.6%에 파손된 URL이 들어 있다. 공지 본문의 신청 링크는 학생이 가장 필요로 하는 값인데 깨진 채로 답변에 나간다.
- 번호 목록에서 항목 번호와 내용이 분리된다(`1.\n학생`). "제N호" 단위 어휘 매칭이 깨진다.
- 문단·절 경계가 사라져 모든 데이터셋이 문장 단위 슬라이딩으로 퇴화한다.

#### 조치 상태 (2026-09-22, 코드 반영·재색인 전)

`strip_html`과 `normalize_whitespace`를 고쳤다. 재색인은 하지 않았으므로 **운영 인덱스는 아직 이전 텍스트**이며, 다음 수집 또는 재색인 때 반영된다.

- URL·이메일·호스트명을 구두점 정규화 앞에서 보호하고 마지막에 복원한다.
- 줄바꿈을 조건부로 접는다. 앞줄이 종결되거나 뒷줄이 항목으로 시작하면 경계를 남긴다. 콜론은 라벨과 값을 붙여 두기 위해 종결로 보지 않는다.
- 빈 줄(`\n\n`)을 보존하고, HTML 블록 요소 경계를 빈 줄로 내보낸다.
- 숫자·한글 순서표(`1.`, `가.`)를 문장 분리 전에 줄머리로 올려 보호한다.
- `chunk_text`의 중복 호출은 남겨 두었다. rules·courses처럼 앞 단계 정규화가 없는 데이터셋은 이 호출이 유일한 정규화 지점이고, 두 번 적용해도 결과가 같다는 것을 테스트로 고정했다.

DB 원문에 적용해 측정한 결과(공지 앞 3,000건, 학칙 전체; 인메모리, 저장 없음):

| 지표 | 공지 전 | 공지 후 | 학칙 전 | 학칙 후 |
|---|---:|---:|---:|---:|
| URL 포함 문서 중 파손 | 663/684 | 14/684 | 19/19 | 9/19 |
| 줄 끝에 번호만 남은 곳 | 1,761 | 7 | 4,384 | 3 |
| 구조 경계에서 시작하는 청크(600/80 분할) | 10.8% | 39.8% | 14.2% | 32.7% |
| 청크 수(600/80 분할) | 10,254 | 9,722 | 8,659 | 8,640 |

남은 URL 파손은 대부분 PDF 추출 단계에서 이미 `ht t p: / /`처럼 깨져 들어온 원문이라 이 단계에서 고칠 수 없다. 학칙은 원문 대부분이 줄바꿈 없는 한 줄이라, 구조 분할의 본격적인 개선은 P0-3(조문 단위 분할)에서 이뤄진다.

동작이 바뀐 기존 테스트가 하나 있다. `"1. 신청 대상 2. 신청 기간"`의 기대값이 `"1.\n신청 대상 2.\n신청 기간"`(번호가 줄 끝에 고립)에서 `"1. 신청 대상\n2. 신청 기간"`으로 바뀌었다.

### P0-2. courses 청크에 파이프라인 내부 필드가 그대로 임베딩된다

`build_course_chunks`가 `for col, value in row.items()`로 정본 payload의 **모든 컬럼**을 본문에 찍는다([ingest.py:1685](../../src/pipelines/ingest.py)). 제외 목록은 4개뿐이다.

```
[문서: 4차 산업사회와 빅데이터 · 학사시기: 2026학년도]

[4차 산업사회와 빅데이터]

availability_status: curriculum_only collected_at: 2026-08-23T18:04:06+00:00
collection_status: fresh college_name: 경영대학 course_code_conflict: False
4차 산업사회와 빅데이터 course_type: 전공 기초과정 curriculum_title: 교과과정 > 교과목 이수
curriculum_url: https: / / mis. dongguk. edu/ page/ 411 data_quality_score: 60 ...
```

- 청크 77.4%가 이런 내부 필드를 포함한다(평균 3.79종).
- 중앙값 400자 중 앞 200자가 bookkeeping이라 실제 교과 설명이 뒤로 밀리거나 다음 청크로 잘린다.
- `collected_at`이 본문에 있어 **내용이 바뀌지 않아도 수집할 때마다 청크 텍스트가 바뀐다.** 정본 `content_hash`는 `collected_at`을 제외하는데 청크 텍스트는 제외하지 않아, 계층 간 규칙 불일치로 전량 재임베딩이 발생한다.
- 하이브리드 검색이 `chunk_text`를 반환하므로 이 내용이 LLM 프롬프트에도 들어간다.

### P0-3. rules에 조문 구조가 반영되지 않는다

학칙 625건에 `제N조` 마커가 18,515개 있다(532개 문서). 조문이 학칙 검색의 자연스러운 단위인데 현재는 600자 고정 분할이다. 결과적으로 8,570청크 중 **86.3%가 조/장 경계가 아닌 문장 중간에서 시작**한다. 특정 조문을 묻는 질의에서 해당 조문이 반토막 나거나 인접 조문과 섞인다.

### P1-4. 짧은 데이터셋은 보일러플레이트가 본문을 압도한다

제목이 `retrieval_text`에 최대 3회 반복된다. `enrich_retrieval_fields`의 `[문서: X …]` 헤더, `to_chunks`의 `[X]` 접두([preprocess.py:246](../../src/utils/preprocess.py)), 그리고 본문 자체다.

```
[문서: 동국대학교 - 동국대학교]

[동국대학교 - 동국대학교]

소속: 동국대학교
정보: 동국대학교 윤** 총장
전화번호: 02)2260-3888
```

staff 4,303건 전체가 이 패턴이라 컬렉션 내부 벡터 구분력이 크게 떨어진다. schedule은 `\n\n` 붕괴까지 겹쳐 필드가 붙는다.

```
학사일정: 2026년 가을 학위수여식(WISE캠퍼스)2026년 가을 학위수여식(WISE캠퍼스)기간: 2026-08-21
```

### P1-5. staff 본문이 라벨 없는 blob이다

`content = " ".join(info_parts)`라 이름·직위·담당업무가 구분 없이 이어진다([ingest.py:1896](../../src/pipelines/ingest.py)). 조직 트리가 `title`과 완전히 중복되는 반면, 담당업무에는 라벨이 없다. 이름은 원천에서 `김**`로 마스킹되어 이름 기반 조회는 원천적으로 불가능하다.

### P1-6. notices 청크 12%가 빈 본문 폴백이다

1,445 / 11,951 청크가 "본문이 비어 있어 상세 내용은 공지 링크를 확인하세요"이고, 그중 914건은 첨부조차 없다. `has_substantive_body` 플래그는 생성되지만 인덱스에는 그대로 올라간다.

## 기능 개선 작업

### P0

- ~~`normalize_whitespace`에서 구조 파괴 부분을 분리한다.~~ 코드 반영 완료(재색인 대기). 위 P0-1 조치 상태 참조.
- `build_course_chunks`를 화이트리스트 투영으로 뒤집는다. 사람이 읽는 필드(교과목명·학수번호·학점·이수구분·개설학기·학과·설명)만 한글 라벨로 찍고 bookkeeping은 메타데이터로만 남긴다. `collected_at` 제거로 재임베딩 churn도 함께 해소된다.
- rules를 조문 단위로 1차 분할하고, 긴 조문만 2차로 나누되 조 제목을 각 조각에 붙인다.

### P1

- 제목 중복을 제거한다. `retrieval_context` 헤더가 이미 `문서: {title}`을 담으므로 `to_chunks(include_title=...)`와 역할을 하나로 통일한다.
- staff/schedule을 라벨 있는 템플릿으로 재작성하고, schedule은 `chunk_size=None`으로 1레코드 = 1청크를 보장한다(현재 102건은 실제 분할이 일어나지 않아 전환 위험이 없다).
- 본문·첨부가 모두 없는 공지 914건은 인덱스에서 제외하거나 저순위 컬렉션으로 분리한다.

### P2

- 데이터셋별 청크 표현 계약(필수 필드, 금지 필드, 분할 단위)을 테스트로 고정해 크롤러 컬럼 추가가 조용히 임베딩 텍스트로 새지 않게 한다.
- 청크 텍스트에도 정본과 동일한 "retrieval-affecting 필드만 해시" 규칙을 적용해 변경분만 재임베딩한다.

## 완료 조건

- 어떤 데이터셋에서도 청크 본문에 `collected_at`, `collection_status`, `data_quality_score` 같은 내부 필드가 나타나지 않는다.
- URL을 포함한 청크에서 `https: / /` 형태의 파손이 0%다.
- rules 청크의 대다수가 조/장 경계에서 시작한다.
- staff/schedule의 보일러플레이트 비율이 본문 대비 절반 미만이다.
- 위 변경 전후로 동일 골든셋·qrels에서 검색 지표를 측정해 회귀가 없음을 보인다.

## 재현 명령

변경 전후 표현 품질을 같은 기준으로 비교하려면 다음을 실행한다.

```bash
cd src/RAG
.venv/bin/python - <<'PY'
import pandas as pd, glob, os, re
mangled = re.compile(r"https?: / /|\. dongguk")
book = ["collected_at:", "collection_status:", "data_quality_score:",
        "course_code_conflict:", "availability_status:", "curriculum_url:"]
for p in sorted(glob.glob("artifacts/chunks/*.parquet")):
    if "pre-recovery" in p:
        continue
    name = os.path.basename(p).replace(".parquet", "")
    df = pd.read_parquet(p)
    ct = df["chunk_text"].fillna("").astype(str)
    rt = df["retrieval_text"].fillna("").astype(str)
    hdr = df["retrieval_context"].fillna("").astype(str)
    title = df["title"].fillna("").astype(str)
    boiler = (hdr.str.len() + title.str.len() * 2 + 8) / rt.str.len().clip(lower=1)
    print(f"{name:9s} chunks={len(df):6d} p50={int(ct.str.len().median()):4d} "
          f"boiler={boiler.clip(upper=1).mean()*100:5.1f}% "
          f"mangled_url={rt.str.contains(mangled).mean()*100:5.1f}% "
          f"bookkeeping={rt.apply(lambda t: any(k in t for k in book)).mean()*100:5.1f}%")
PY
```

학칙 조문 구조는 정본에서 직접 센다.

```bash
.venv/bin/python - <<'PY'
import sqlite3, re
rows = sqlite3.connect("rag_database.db").execute(
    "select full_text from rules where full_text is not null").fetchall()
pat = re.compile(r"제\s*\d+\s*조")
print("docs:", len(rows),
      "docs with 제N조:", sum(1 for r in rows if pat.search(r[0] or "")),
      "total markers:", sum(len(pat.findall(r[0] or "")) for r in rows))
PY
```
