"""밀집 임베딩+BM25 하이브리드 검색 유틸리티입니다."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple, Dict

import joblib
import chromadb
import numpy as np
import pandas as pd
import sklearn
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.config import (
    DEFAULT_TOP_K,
    HYBRID_ALPHA,
    HYBRID_FUSION_MODE,
    HYBRID_RRF_K,
    TFIDF_TOKENIZER,
    TFIDF_REQUIRE_MANIFEST,
    TFIDF_VERIFY_INTEGRITY,
    VECTORIZER_DIR,
)
from src.models.embedding import encode_queries
from src.vectorstore.chroma_client import get_collection, query_items

logger = logging.getLogger(__name__)

# 희소 검색 pkl 무결성 매니페스트: {파일명: sha256}. 학습 시 갱신하고 로드 전 대조한다.
# 경로는 호출 시점에 VECTORIZER_DIR에서 파생한다(테스트의 VECTORIZER_DIR 패치가 함께 적용되도록).
_MANIFEST_NAME = "manifest.json"
_MANIFEST_LOCK_NAME = "manifest.lock"
_LOCAL_ARTIFACT_LOCK = threading.RLock()
_KIWI = None
_KIWI_UNAVAILABLE = False
_TOKEN_PATTERN = re.compile(r"[가-힣A-Za-z0-9]+")
_QUERY_TITLE_STOPWORDS = {
    "언제", "언제야", "어떻게", "어디", "무엇", "뭐야", "알려줘", "알려주세요",
    "관련", "안내", "공지", "일정", "기간", "학기", "학년도",
}
_KOREAN_SUFFIXES = tuple(
    sorted(
        {
            "으로부터", "에서부터", "에게서", "께서는", "으로는", "으로서", "으로써",
            "까지", "부터", "에게", "한테", "께서", "처럼", "보다", "마다", "조차",
            "마저", "밖에", "에서", "으로", "로서", "로써", "이며", "이고", "하고",
            "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "만",
            "로", "란", "나", "이나", "라도", "이라도", "든지", "인지",
            "입니다", "합니다", "된다", "된다면", "되는", "되어", "하며", "하여", "하고",
        },
        key=len,
        reverse=True,
    )
)


def _bm25_path(identifier: str) -> Path:
    return VECTORIZER_DIR / f"{identifier}_bm25.pkl"


def _legacy_tfidf_path(identifier: str) -> Path:
    return VECTORIZER_DIR / f"{identifier}_tfidf.pkl"


def lexical_artifact_path(identifier: str) -> Path:
    """현재 BM25 아티팩트를 우선하고, 재색인 전에는 TF-IDF를 읽기 전용 폴백한다."""
    bm25_path = _bm25_path(identifier)
    return bm25_path if bm25_path.exists() else _legacy_tfidf_path(identifier)


# 과거 테스트·도구의 내부 패치 지점을 보존한다. 신규 학습은 항상 _bm25_path를 쓴다.
def _vectorizer_path(identifier: str) -> Path:
    return lexical_artifact_path(identifier)


def _manifest_path() -> Path:
    return VECTORIZER_DIR / _MANIFEST_NAME


@contextmanager
def _artifact_lock(*, exclusive: bool):
    """희소 아티팩트와 매니페스트를 하나의 스냅샷처럼 읽고 쓴다.

    스케줄러가 인덱스를 교체하는 동안 다른 스레드/프로세스가 새 pkl과 이전
    매니페스트를 섞어 읽지 않게 한다. ``fcntl``이 없는 플랫폼에서는 최소한
    프로세스 내부 잠금은 유지한다.
    """
    VECTORIZER_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = VECTORIZER_DIR / _MANIFEST_LOCK_NAME
    with _LOCAL_ARTIFACT_LOCK:
        with open(lock_path, "a+b") as lock_file:
            try:
                import fcntl
            except ImportError:  # pragma: no cover - Linux/macOS 배포 경로에는 존재
                yield
                return
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lock_file.fileno(), operation)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_kiwi():
    global _KIWI, _KIWI_UNAVAILABLE
    if _KIWI is not None or _KIWI_UNAVAILABLE:
        return _KIWI
    try:
        from kiwipiepy import Kiwi  # type: ignore

        _KIWI = Kiwi()
    except Exception as exc:  # noqa: BLE001 - optional dependency
        _KIWI_UNAVAILABLE = True
        logger.info("Kiwi tokenizer is unavailable; falling back to lightweight Korean tokenizer: %s", exc)
        return None
    return _KIWI


def _strip_korean_suffix(token: str) -> str:
    for suffix in _KOREAN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _hangul_ngrams(token: str) -> list[str]:
    if not re.fullmatch(r"[가-힣]+", token) or len(token) < 3:
        return []
    grams: list[str] = []
    # Three-syllable compounds such as "개강일" must expose their two-
    # syllable stem ("개강") so conversational particles do not turn an exact
    # title match into an out-of-vocabulary query.
    sizes = (2,) if len(token) == 3 else (2, 3)
    for size in sizes:
        grams.extend(token[idx : idx + size] for idx in range(0, len(token) - size + 1))
    return grams


def _light_korean_tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        token = token.strip().lower()
        if len(token) < 2 or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    for raw in _TOKEN_PATTERN.findall(str(text)):
        normalized = raw.lower()
        add(normalized)
        stripped = _strip_korean_suffix(normalized)
        add(stripped)
        for gram in _hangul_ngrams(stripped):
            add(gram)

    return tokens


def _query_title_focus_score(query: str, title: str) -> float:
    """Return a small domain-agnostic exact-title signal for a query."""
    query_tokens = {
        token
        for token in _light_korean_tokenize(query)
        if token not in _QUERY_TITLE_STOPWORDS and not any(char.isdigit() for char in token)
    }
    if not query_tokens or not title:
        return 0.0
    title_tokens = set(_light_korean_tokenize(title))
    overlap = query_tokens & title_tokens
    if not overlap:
        return 0.0
    longest_query = max(len(token) for token in query_tokens)
    longest_overlap = max(len(token) for token in overlap)
    return min(1.0, longest_overlap / max(longest_query, 1))


def _academic_period_title_adjustment(query: str, title: str) -> float:
    """Prefer notices whose target academic year/semester matches the query."""
    requested_year = re.search(r"\b(20\d{2})\s*(?:학년도|년도|년|-)?", query)
    requested_semester = re.search(r"([12])\s*학기", query)
    if requested_year is None and requested_semester is None:
        return 0.0

    adjustment = 0.0
    title_year = re.search(r"\b(20\d{2})\s*(?:학년도|년도|년|-)?", title)
    title_semester = re.search(r"([12])\s*학기", title)
    if requested_year is not None and title_year is not None:
        adjustment += 0.30 if requested_year.group(1) == title_year.group(1) else -0.30
    if requested_semester is not None and title_semester is not None:
        adjustment += 0.10 if requested_semester.group(1) == title_semester.group(1) else -0.10
    return adjustment


def _kiwi_or_light_korean_tokenize(text: str) -> list[str]:
    kiwi = _load_kiwi()
    if kiwi is None:
        return _light_korean_tokenize(text)

    tokens: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        token = token.strip().lower()
        if len(token) < 2 or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    try:
        for token in kiwi.tokenize(str(text)):
            form = getattr(token, "form", "")
            tag = getattr(token, "tag", "")
            if tag.startswith(("N", "V", "M", "SL", "SN")):
                add(form)
                add(_strip_korean_suffix(form))
                for gram in _hangul_ngrams(form):
                    add(gram)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kiwi tokenization failed; using lightweight tokenizer: %s", exc)
        return _light_korean_tokenize(text)

    return tokens or _light_korean_tokenize(text)


def _resolve_tfidf_tokenizer_name() -> str:
    name = (TFIDF_TOKENIZER or "korean").strip().lower()
    if name in {"default", "sklearn", "word"}:
        return "default"
    if name in {"korean", "ko", "kiwi", "morph", "morpheme"}:
        return "korean"
    logger.warning("Unknown TFIDF_TOKENIZER=%r; using korean tokenizer.", TFIDF_TOKENIZER)
    return "korean"


def build_tfidf_vectorizer() -> TfidfVectorizer:
    """레거시 TF-IDF 아티팩트와 토크나이저 회귀 테스트를 위한 호환 생성기."""
    tokenizer_name = _resolve_tfidf_tokenizer_name()
    if tokenizer_name == "default":
        return TfidfVectorizer(max_features=10000)

    return TfidfVectorizer(
        max_features=10000,
        tokenizer=_kiwi_or_light_korean_tokenize,
        token_pattern=None,
        lowercase=False,
    )


@dataclass
class BM25LexicalIndex:
    """직렬화 가능한 한국어 BM25 검색기."""

    engine: Any
    tokenizer_name: str
    document_count: int
    k1: float = 1.5
    b: float = 0.75

    def score(self, query: str) -> np.ndarray:
        tokens = (
            _kiwi_or_light_korean_tokenize(query)
            if self.tokenizer_name == "korean"
            else _light_korean_tokenize(query)
        )
        if not tokens:
            return np.zeros(self.document_count, dtype=np.float64)
        return np.asarray(self.engine.get_scores(tokens), dtype=np.float64)


def _sha256_file(path: Path) -> str:
    """파일의 SHA-256 16진 다이제스트를 청크 단위로 계산한다(대용량 pkl 대비)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest() -> Dict[str, str]:
    manifest_path = _manifest_path()
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("희소 검색 매니페스트를 읽지 못했습니다(%s): %s", manifest_path, exc)
        return {}


def _write_manifest_unlocked(manifest: Dict[str, str]) -> None:
    manifest_path = _manifest_path()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{manifest_path.name}.",
        suffix=".tmp",
        dir=manifest_path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(manifest_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _update_manifest(filename: str, digest: str) -> None:
    """매니페스트 단독 갱신 호출도 프로세스 간 lost update 없이 처리한다."""
    with _artifact_lock(exclusive=True):
        manifest = _read_manifest()
        manifest[filename] = digest
        _write_manifest_unlocked(manifest)


def _verify_artifact_integrity_unlocked(path: Path) -> None:
    """joblib.load 이전에 매니페스트 해시와 대조한다(불일치 시 fail-closed).

    pkl/joblib 아티팩트는 역직렬화 중 임의 코드를 실행할 수 있으므로, 신뢰된
    매니페스트와 다른 파일은 로드하지 않는다. 검증은 TFIDF_VERIFY_INTEGRITY로 끌 수 있고,
    매니페스트 미등록 항목의 거부 여부는 TFIDF_REQUIRE_MANIFEST로 제어한다.
    """
    if not TFIDF_VERIFY_INTEGRITY:
        return
    manifest = _read_manifest()
    expected = manifest.get(path.name)
    if expected is None:
        msg = (
            f"희소 검색 아티팩트 '{path.name}'가 무결성 매니페스트에 없습니다. "
            f"scripts/build_indices.py 재생성 또는 매니페스트 갱신이 필요합니다."
        )
        if TFIDF_REQUIRE_MANIFEST:
            raise ValueError(msg + " (TFIDF_REQUIRE_MANIFEST=1 — 로드 거부)")
        logger.warning(msg + " (검증 없이 로드)")
        return
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"희소 검색 아티팩트 '{path.name}' 무결성 검증 실패: "
            f"매니페스트 해시와 불일치(변조/손상 가능). 로드를 거부합니다."
        )


def _verify_artifact_integrity(path: Path) -> None:
    with _artifact_lock(exclusive=False):
        _verify_artifact_integrity_unlocked(path)


def train_bm25(
    identifier: str,
    corpus: Iterable[str],
    chunk_ids: Iterable[str] | None = None,
) -> Tuple[BM25LexicalIndex, np.ndarray]:
    """한국어 토큰을 재사용해 BM25 인덱스를 학습하고 저장한다.

    chunk_ids를 함께 주면 행→chunk_id 매핑이 아티팩트에 저장되어,
    검색 시 chunks_df 행 순서에 의존하지 않고 점수를 매핑할 수 있습니다.
    """
    texts = list(corpus)
    if not texts:
        raise ValueError("Corpus is empty, cannot train BM25 index.")
    ids = [str(cid) for cid in chunk_ids] if chunk_ids is not None else None
    if ids is not None and len(ids) != len(texts):
        raise ValueError(
            f"chunk_ids length ({len(ids)}) does not match corpus length ({len(texts)})."
        )
    tokenizer_name = _resolve_tfidf_tokenizer_name()
    tokenizer = (
        _kiwi_or_light_korean_tokenize
        if tokenizer_name == "korean"
        else _light_korean_tokenize
    )
    tokenized_corpus = [tokenizer(text) for text in texts]
    engine = BM25Okapi(tokenized_corpus, k1=1.5, b=0.75)
    vectorizer = BM25LexicalIndex(
        engine=engine,
        tokenizer_name=tokenizer_name,
        document_count=len(texts),
    )
    # 기존 호출부가 행 수 정합성을 matrix.shape[0]으로 확인한다. BM25 점수는
    # engine이 계산하므로 0열 행렬이면 충분하고, 문서 본문을 중복 저장하지 않는다.
    matrix = np.empty((len(texts), 0), dtype=np.float32)
    path = _bm25_path(identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        joblib.dump(
            {
                "vectorizer": vectorizer,
                "matrix": matrix,
                "chunk_ids": ids,
                "metadata": {
                    "dataset": identifier,
                    "document_count": len(texts),
                    "created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
                    "sklearn_version": sklearn.__version__,
                    "retriever_type": "bm25",
                    "bm25_k1": vectorizer.k1,
                    "bm25_b": vectorizer.b,
                    "tokenizer": tokenizer_name,
                    "tokenizer_backend": (
                        "kiwi"
                        if tokenizer_name == "korean" and _load_kiwi() is not None
                        else "light_korean" if tokenizer_name == "korean"
                        else tokenizer_name
                    ),
                },
            },
            tmp_path,
        )
        digest = _sha256_file(tmp_path)
        # pkl 교체와 매니페스트 교체를 같은 배타 잠금 안에서 수행한다. 독자는
        # 공유 잠금을 잡으므로 두 세대가 섞인 중간 상태를 관찰하지 않는다.
        with _artifact_lock(exclusive=True):
            manifest = _read_manifest()
            tmp_path.replace(path)
            manifest[path.name] = digest
            _write_manifest_unlocked(manifest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return vectorizer, matrix


def _load_lexical_artifact(identifier: str) -> dict:
    path = lexical_artifact_path(identifier)
    # 검증부터 역직렬화 완료까지 공유 잠금을 유지해 검증 후 파일 교체(TOCTOU)도 막는다.
    with _artifact_lock(exclusive=False):
        _verify_artifact_integrity_unlocked(path)
        artifact = joblib.load(path)
    if isinstance(artifact, dict) and "vectorizer" in artifact and "matrix" in artifact:
        metadata = artifact.get("metadata")
        if isinstance(metadata, dict):
            return artifact
        return {
            "vectorizer": artifact["vectorizer"],
            "matrix": artifact["matrix"],
            "chunk_ids": artifact.get("chunk_ids"),
            "metadata": {
                "dataset": identifier,
                "document_count": None,
                "created_at": None,
                "sklearn_version": None,
                "retriever_type": "tfidf",
                "is_legacy": True,
            },
        }

    raise ValueError(f"Unexpected lexical artifact format for '{identifier}'.")


def load_lexical(identifier: str) -> Tuple[Any, np.ndarray]:
    """BM25 인덱스를 불러오며, 재색인 전에는 레거시 TF-IDF를 허용한다."""
    data = _load_lexical_artifact(identifier)
    return data["vectorizer"], data["matrix"]


def load_lexical_with_ids(identifier: str) -> Tuple[Any, np.ndarray, Optional[List[str]]]:
    """희소 검색기와 행→chunk_id 매핑을 불러온다."""
    data = _load_lexical_artifact(identifier)
    return data["vectorizer"], data["matrix"], data.get("chunk_ids")


def score_lexical_query(vectorizer: Any, matrix: np.ndarray, query: str) -> np.ndarray:
    """Return normalized per-row scores for BM25 or legacy TF-IDF."""
    if isinstance(vectorizer, BM25LexicalIndex):
        raw_scores = np.asarray(vectorizer.score(query), dtype=np.float64)
        positive_max = float(np.max(raw_scores)) if raw_scores.size else 0.0
        scores = (
            np.clip(raw_scores / positive_max, 0.0, 1.0)
            if positive_max > 0
            else np.zeros_like(raw_scores)
        )
    else:
        query_vector = vectorizer.transform([query])
        scores = np.asarray(
            cosine_similarity(query_vector, matrix).ravel(),
            dtype=np.float64,
        )

    matrix_rows = int(getattr(matrix, "shape", (0,))[0])
    if scores.shape[0] != matrix_rows:
        raise ValueError(
            f"lexical score rows ({scores.shape[0]}) do not match matrix rows ({matrix_rows})"
        )
    return scores


def read_lexical_metadata(identifier: str) -> Dict:
    """희소 검색 아티팩트 메타데이터를 읽는다."""
    data = _load_lexical_artifact(identifier)
    metadata = data.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


# 외부 스크립트 호환. 신규 인덱스는 이름과 무관하게 BM25로 생성된다.
train_tfidf = train_bm25
load_tfidf = load_lexical
load_tfidf_with_ids = load_lexical_with_ids
read_tfidf_metadata = read_lexical_metadata


def _matches_where(row: pd.Series, where_filter: Dict) -> bool:
    """Chroma 스타일 where 필터({"key": {"$eq": v}} / {"$and": [...]})를 DataFrame 행에 평가합니다."""
    for key, condition in where_filter.items():
        if key == "$and":
            if not all(_matches_where(row, sub) for sub in condition):
                return False
            continue
        if key == "$or":
            if not any(_matches_where(row, sub) for sub in condition):
                return False
            continue
        value = row.get(key) if key in row.index else None
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op == "$eq":
                    if str(value) != str(expected):
                        return False
                elif op == "$ne":
                    if str(value) == str(expected):
                        return False
                elif op == "$in":
                    if str(value) not in {str(e) for e in expected}:
                        return False
                else:
                    # 지원하지 않는 연산자는 보수적으로 불일치 처리
                    return False
        else:
            if str(value) != str(condition):
                return False
    return True


def hybrid_search(
    collection_name: str,
    chunks_df: pd.DataFrame,
    tfidf_vectorizer: TfidfVectorizer,
    tfidf_matrix,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = HYBRID_ALPHA,
    where_filter: Dict | None = None,
    tfidf_chunk_ids: List[str] | None = None,
    academic_period_query: str | None = None,
) -> pd.DataFrame:
    """
    최적화된 하이브리드 검색:
    1. Vector Search로 상위 N개 검색
    2. TF-IDF Search로 상위 N개 검색
    3. 결과 결합 및 재정렬 (Rerank)

    tfidf_chunk_ids: TF-IDF 행렬의 행→chunk_id 매핑(아티팩트에 저장된 것).
    주어지면 chunks_df 행 순서에 의존하지 않고 sparse 점수를 매핑합니다.
    """
    if chunks_df.empty:
        return chunks_df.copy()

    # 검색 후보 수 (Top-K보다 넉넉하게 가져와서 랭킹)
    limit = top_k * 5
    
    # 1. Vector Search (Dense). A missing/corrupt Chroma segment must not take
    # the valid TF-IDF path down with it; readiness still exposes the degraded
    # state while the request continues with sparse retrieval.
    vec_scores: Dict[str, float] = {}
    try:
        collection = get_collection(collection_name)
        query_embedding = encode_queries([query])
        vec_results = query_items(
            collection_name,
            collection=collection,
            query_embeddings=query_embedding,
            n_results=limit,
            where=where_filter,
        )

        vec_ids = (vec_results.get("ids") or [[]])[0]
        vec_dists = (vec_results.get("distances") or [[]])[0]

        # 거리(Distance)를 유사도(Similarity)로 변환 — 컬렉션의 실제 메트릭에 맞게 처리.
        # cosine/ip: dist = 1 - sim → sim = 1 - dist
        # l2(Chroma는 squared L2 반환): 정규화 임베딩이면 dist = 2 - 2cos → sim = 1 - dist/2
        space = (getattr(collection, "metadata", None) or {}).get("hnsw:space", "l2")
        if space in ("cosine", "ip"):
            _to_sim = lambda d: 1.0 - d
        else:
            _to_sim = lambda d: 1.0 - d / 2.0
        vec_scores = {cid: max(0.0, _to_sim(dist)) for cid, dist in zip(vec_ids, vec_dists)}
    except chromadb.errors.ChromaError as exc:
        logger.warning(
            "Dense retrieval unavailable for collection '%s'; continuing sparse-only (%s: %s).",
            collection_name,
            type(exc).__name__,
            exc,
        )

    # 2. Sparse Search (BM25; 재색인 전에는 레거시 TF-IDF 호환)
    # 행→chunk_id 매핑: 아티팩트의 chunk_ids가 있으면 그것을 사용(행 순서 결합 제거),
    # 없으면(구버전 아티팩트) 행 수가 chunks_df와 일치할 때만 행 순서로 매핑.
    matrix_rows = tfidf_matrix.shape[0]
    row_ids: List[str] | None = None
    if tfidf_chunk_ids is not None and len(tfidf_chunk_ids) == matrix_rows:
        row_ids = [str(cid) for cid in tfidf_chunk_ids]
    elif matrix_rows == len(chunks_df):
        row_ids = chunks_df["chunk_id"].astype(str).tolist()
    else:
        logging.warning(
            "TF-IDF matrix rows (%d) do not match chunks_df rows (%d) for collection '%s' "
            "and no chunk_ids mapping is available — skipping sparse scoring (vector-only).",
            matrix_rows,
            len(chunks_df),
            collection_name,
        )

    sparse_scores: Dict[str, float] = {}
    if row_ids is not None:
        sparse_sims = score_lexical_query(tfidf_vectorizer, tfidf_matrix, query)
        sparse_indices = np.argsort(sparse_sims)[::-1][:limit]
        for idx in sparse_indices:
            if sparse_sims[idx] > 0:
                sparse_scores[str(row_ids[idx])] = sparse_sims[idx]
        if not sparse_scores:
            # OOV 쿼리 등으로 sparse 기여가 0이면 무음으로 vector-only가 되므로 흔적을 남긴다
            logging.info(
                "Sparse retrieval returned no positive scores for query %r on '%s' (vector-only search).",
                query, collection_name,
            )

    # 3. Merge & Fusion
    # 후보군: Vector 검색 결과 OR TF-IDF 검색 결과.
    # where_filter가 있으면 벡터 검색은 Chroma에서 이미 필터링됨.
    # sparse-only 히트는 chunks_df 메타데이터로 필터를 직접 검증해 통과한 것만 후보로 살린다
    # (이전에는 벡터 결과와의 intersection만 허용해 키워드-only 히트가 전부 버려졌음).
    if where_filter:
        sparse_only = set(sparse_scores.keys()) - set(vec_scores.keys())
        candidate_ids = set(vec_scores.keys())
        if sparse_only:
            id_to_pos = {
                str(cid): pos
                for pos, cid in enumerate(chunks_df["chunk_id"].astype(str).tolist())
            }
            for cid in sparse_only:
                pos = id_to_pos.get(cid)
                if pos is None:
                    continue
                if _matches_where(chunks_df.iloc[pos], where_filter):
                    candidate_ids.add(cid)
    else:
        candidate_ids = set(vec_scores.keys()) | set(sparse_scores.keys())

    id_to_pos = {
        str(cid): pos
        for pos, cid in enumerate(chunks_df["chunk_id"].astype(str).tolist())
    }
    dense_ranks = {
        chunk_id: rank
        for rank, (chunk_id, _) in enumerate(
            sorted(vec_scores.items(), key=lambda item: item[1], reverse=True),
            start=1,
        )
    }
    sparse_ranks = {
        chunk_id: rank
        for rank, (chunk_id, _) in enumerate(
            sorted(sparse_scores.items(), key=lambda item: item[1], reverse=True),
            start=1,
        )
    }
    hybrid_results = []
    for cid in candidate_ids:
        v_score = vec_scores.get(cid, 0.0)
        s_score = sparse_scores.get(cid, 0.0)
        
        # 기본 가중 합산에 더해 정확한 어휘 일치를 위한 lexical guard를 둔다.
        # Dense 점수가 높은 관련 문서가 학기/날짜/제도명까지 정확히 일치하는
        # TF-IDF 문서를 밀어내지 않게 하되, sparse 점수가 약한 문서는 기존
        # hybrid 점수를 그대로 사용한다. 도메인별 예외어 없이 모든 코퍼스에
        # 동일하게 적용한다.
        if HYBRID_FUSION_MODE == "rrf":
            rrf_score = 0.0
            if cid in dense_ranks:
                rrf_score += 1.0 / (HYBRID_RRF_K + dense_ranks[cid])
            if cid in sparse_ranks:
                rrf_score += 1.0 / (HYBRID_RRF_K + sparse_ranks[cid])
            # 두 랭킹 모두 1위일 때 1.0이 되도록 정규화해 기존 임계값 척도를 유지한다.
            weighted_score = rrf_score / (2.0 / (HYBRID_RRF_K + 1))
        else:
            weighted_score = alpha * v_score + (1.0 - alpha) * s_score
        # Sparse and dense similarities are both cosine-like scores in [0, 1],
        # so the sparse score itself is a valid lower bound for an exact-word
        # match. The weighted score can still win whenever semantic evidence is
        # stronger.
        lexical_guard_score = s_score
        title_score = 0.0
        title = ""
        row_position = id_to_pos.get(str(cid))
        if row_position is not None:
            row = chunks_df.iloc[row_position]
            title = row.get("title") if "title" in row.index else None
            if not isinstance(title, str) or not title.strip():
                title = _extract_title(str(row.get("chunk_text", "")))
            title_score = _query_title_focus_score(query, title)
        period_adjustment = (
            _academic_period_title_adjustment(
                academic_period_query if academic_period_query is not None else query,
                title,
            )
            if "notice" in collection_name.lower()
            else 0.0
        )
        final_score = max(weighted_score, lexical_guard_score) + 0.18 * title_score + period_adjustment
        hybrid_results.append((cid, final_score, v_score, s_score))
    
    # 점수순 정렬
    hybrid_results.sort(key=lambda x: x[1], reverse=True)
    top_results = hybrid_results[:top_k]
    
    # 결과 DataFrame 생성
    top_ids = [res[0] for res in top_results]
    score_by_id = {
        res[0]: {
            "hybrid_score": res[1],
            "vector_score": res[2],
            "sparse_score": res[3],
        }
        for res in top_results
    }
    
    # 원본 DataFrame에서 해당 ID를 가진 행 추출 및 순서 유지
    # set_index를 사용하여 빠르게 조회 (중복 chunk_id는 첫 행만 유지해 .loc 다중행 매칭 방지)
    df_indexed = (
        chunks_df.assign(chunk_id=chunks_df["chunk_id"].astype(str))
        .drop_duplicates(subset="chunk_id", keep="first")
        .set_index("chunk_id")
    )
    
    # top_ids가 chunks_df에 없는 경우(삭제된 문서 등) 방지
    valid_ids = [cid for cid in top_ids if cid in df_indexed.index]
    
    if not valid_ids:
        # 원본 DataFrame의 구조를 유지한 빈 DataFrame 반환
        return chunks_df.iloc[:0].copy()
        
    result_df = df_indexed.loc[valid_ids].copy()
    result_df["hybrid_score"] = [score_by_id[cid]["hybrid_score"] for cid in valid_ids]
    result_df["vector_score"] = [score_by_id[cid]["vector_score"] for cid in valid_ids]
    result_df["sparse_score"] = [score_by_id[cid]["sparse_score"] for cid in valid_ids]
    result_df = result_df.reset_index() # chunk_id를 다시 컬럼으로
    
    return result_df


def hybrid_search_with_meta(
    collection_name: str,
    chunks_df: pd.DataFrame,
    tfidf_vectorizer: TfidfVectorizer,
    tfidf_matrix,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = HYBRID_ALPHA,
    where_filter: Dict | None = None, # where_filter 추가
    tfidf_chunk_ids: List[str] | None = None,
    academic_period_query: str | None = None,
) -> pd.DataFrame:
    """노트북과 같은 형식으로 메타데이터 열을 청크 텍스트와 함께 반환합니다."""
    hits = hybrid_search(
        collection_name,
        chunks_df,
        tfidf_vectorizer,
        tfidf_matrix,
        query,
        top_k,
        alpha,
        where_filter,
        tfidf_chunk_ids,
        academic_period_query,
    )
    out = hits.copy()
    out["title"] = out["chunk_text"].apply(_extract_title)
    for column in ("topics", "category", "published_at", "apply_deadline", "url", "source", "notice_id"):
        if column not in out.columns:
            out[column] = ""
        else:
            out[column] = out[column].fillna("")
    # Provenance, campus identity, and effective-date fields are part of the
    # answer/source contract. Dropping them here silently turns valid official
    # evidence into unknown-campus or undated evidence downstream.
    desired = [
        "chunk_id", "title", "chunk_text", "hybrid_score", "vector_score", "sparse_score",
        "topics", "category", "published_at", "apply_deadline", "url", "source", "notice_id",
        "major", "college_name", "entry_year", "source_type", "attachments",
        "course_code", "credit", "grade", "semester", "course_type",
        "curriculum_year", "source_page", "source_priority", "course_code_conflict",
        "availability_status", "data_quality_score", "collection_status",
        "doc_id", "position",  # parent-document 확장(이웃 청크 결합)에 사용
        "is_closed", "restaurant", "meal_date",  # 학식: 휴무 패널티·식당/날짜 표시에 사용
        "schedule_start", "schedule_end", "department", "campus_scope",
        "filename", "relative_dir", "source_file", "document_key", "source_id",
        "board_code", "article_id", "schedule_id", "staff_id", "course_id", "rule_id",
        "canonical_key", "is_latest",
        "title_norm", "audience", "retrieval_context",
        "staff_position", "staff_role", "staff_phone",  # 연락처 질의 순위 판단에 사용
        "has_substantive_body",  # 제목만 있는 공지를 근거 자리에서 뒤로 미는 데 사용
    ]
    existing = [col for col in desired if col in out.columns]
    return out[existing]


def _extract_title(text: str) -> str:
    """청크 첫 줄의 `[제목]` 래퍼를 벗겨 원래 제목을 돌려준다.

    학교 공지 제목은 `[홍보] 2026년 …`처럼 대괄호 접두어로 시작하는 것이 흔하다
    (5,528건 중 1,618건, 28.9%). 첫 `]`에서 자르면 그런 제목이 전부 "홍보"가 되어
    출처 표시가 무의미해지고, 제목 일치 보너스도 잘린 조각으로 계산된다.
    청크는 `[제목]\\n\\n본문` 형태이므로 첫 줄 전체를 래퍼로 보고 마지막 `]`까지 벗긴다.
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    first_line = text.split("\n", 1)[0].strip()
    if first_line.startswith("[") and first_line.endswith("]") and len(first_line) > 2:
        return first_line[1:-1].strip()[:120]
    if first_line.startswith("[") and "]" in first_line:
        return first_line[1 : first_line.rindex("]")].strip()[:120]
    return first_line[:120]


__all__ = [
    "BM25LexicalIndex",
    "train_bm25",
    "load_lexical",
    "load_lexical_with_ids",
    "score_lexical_query",
    "read_lexical_metadata",
    "lexical_artifact_path",
    "train_tfidf",
    "load_tfidf",
    "load_tfidf_with_ids",
    "read_tfidf_metadata",
    "build_tfidf_vectorizer",
    "hybrid_search",
    "hybrid_search_with_meta",
]
