"""Data ingestion routines that build Chroma indices for multiple datasets."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd

from src.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHUNKS_DIR,
    DATA_SOURCES,
)
from src.models.embedding import encode_texts
from src.search.hybrid import train_tfidf
from src.utils.preprocess import (
    apply_cleaning,
    make_doc_id,
    to_chunks,
)
from src.vectorstore.chroma_client import add_items, reset_collection


@dataclass
class DatasetArtifacts:
    key: str
    collection: str
    chunk_path: Path

    @property
    def csv_path(self) -> Path:
        return self.chunk_path.with_suffix(".csv")


DATASET_ARTIFACTS: Dict[str, DatasetArtifacts] = {
    "notices": DatasetArtifacts(
        key="notices",
        collection="dongguk_notices",
        chunk_path=CHUNKS_DIR / "notices.parquet",
    ),
    "rules": DatasetArtifacts(
        key="rules",
        collection="dongguk_rules",
        chunk_path=CHUNKS_DIR / "rules.parquet",
    ),
    "schedule": DatasetArtifacts(
        key="schedule",
        collection="dongguk_schedule",
        chunk_path=CHUNKS_DIR / "schedule.parquet",
    ),
    "courses": DatasetArtifacts(
        key="courses",
        collection="dongguk_courses",
        chunk_path=CHUNKS_DIR / "courses.parquet",
    ),
}


def _persist_chunks(key: str, collection: str, chunks_df: pd.DataFrame) -> Tuple[pd.DataFrame, object, object]:
    if chunks_df.empty:
        raise ValueError(f"Dataset '{key}' produced no chunks; check the source CSV.")

    embeddings = encode_texts(chunks_df["chunk_text"].tolist())

    reset_collection(collection)
    add_items(
        collection,
        ids=chunks_df["chunk_id"],
        documents=chunks_df["chunk_text"],
        metadatas=chunks_df.drop(columns=["chunk_text"]).to_dict(orient="records"),
        embeddings=embeddings,
    )

    artifacts = DATASET_ARTIFACTS[key]
    artifacts.chunk_path.parent.mkdir(parents=True, exist_ok=True)

    write_path = artifacts.chunk_path
    try:
        chunks_df.to_parquet(write_path, index=False)
    except (ImportError, ModuleNotFoundError, ValueError, OSError):
        write_path = artifacts.csv_path
        chunks_df.to_csv(write_path, index=False, encoding="utf-8-sig")
    artifacts.chunk_path = write_path

    vectorizer, matrix = train_tfidf(key, chunks_df["chunk_text"].tolist())
    return chunks_df, vectorizer, matrix


def build_notice_chunks(df: pd.DataFrame) -> pd.DataFrame:
    column = {
        "title": "제목",
        "content": "본문",
        "date": "게시일",
        "topic": "게시판",
        "url": "상세URL",
        "attachment": "첨부파일",
    }

    cleaned = apply_cleaning(df, content_col=column["content"], date_col=column["date"])

    docs: List[dict] = []
    for _, row in cleaned.iterrows():
        text = row.get("clean_text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        published = row.get("clean_date")
        doc_id = make_doc_id(row.get(column["title"]), row.get(column["topic"]), published)
        docs.append(
            {
                "doc_id": doc_id,
                "title": row.get(column["title"], ""),
                "text": text,
                "topics": row.get(column["topic"], ""),
                "published_at": published or "",
                "url": row.get(column["url"], ""),
                "attachments": row.get(column["attachment"], ""),
                "source": "notices",
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        include_title=True,
    )
    return pd.DataFrame(chunks)


def ingest_notices() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["notices"]
    if not path.exists():
        raise FileNotFoundError(f"Notice CSV not found: {path}")

    raw_df = pd.read_csv(path)
    chunks_df = build_notice_chunks(raw_df)
    return _persist_chunks("notices", DATASET_ARTIFACTS["notices"].collection, chunks_df)


def ingest_rules() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["rules"]
    if not path.exists():
        raise FileNotFoundError(f"Rule CSV not found: {path}")

    df = pd.read_csv(path).fillna("").astype(str)

    docs: List[dict] = []
    for _, row in df.iterrows():
        text = str(row.get("text", "")).strip()
        if not text:
            text = str(row.get("filename", "")).strip()
        if not text:
            continue
        filename = row.get("filename", "")
        rel_dir = row.get("relative_dir", "")
        doc_id = make_doc_id("rules", rel_dir, filename)
        docs.append(
            {
                "doc_id": doc_id,
                "title": filename or text[:80] or "학칙 문서",
                "text": text,
                "topics": "규정",
                "relative_dir": rel_dir,
                "filename": filename,
                "source": "rules",
                "url": "",
                "published_at": "",
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        include_title=True,
    )
    chunks_df = pd.DataFrame(chunks)
    return _persist_chunks("rules", DATASET_ARTIFACTS["rules"].collection, chunks_df)


def ingest_schedule() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["schedule"]
    if not path.exists():
        raise FileNotFoundError(f"Schedule CSV not found: {path}")

    df = pd.read_csv(path).fillna("").astype(str)

    docs: List[dict] = []
    for _, row in df.iterrows():
        text_segments: List[str] = []
        for col, value in row.items():
            col_name = str(col).strip()
            value_str = str(value).strip()
            if col_name in {"start", "end"}:
                continue
            if col_name.lower().startswith("unnamed"):
                continue
            if not value_str:
                continue
            if col_name in {"내용", "일정", "event", "2", "description"}:
                text_segments.append(value_str)
            else:
                text_segments.append(f"{col_name}: {value_str}")

        text = "\n".join(text_segments).strip()
        if not text:
            continue

        title = text_segments[0] if text_segments else "학사 일정"
        category = str(row.get("구분", row.get("0", ""))).strip()
        if category.lower() == "nan":
            category = ""
        department = str(row.get("주관부서", "")).strip()
        if department.lower() == "nan":
            department = ""

        doc_id = make_doc_id("schedule", row.get("start"), row.get("end"), text)
        docs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text": text,
                "schedule_start": row.get("start", ""),
                "schedule_end": row.get("end", ""),
                "category": category,
                "department": department,
                "topics": category or "schedule",
                "source": "schedule",
                "url": "",
                "published_at": row.get("start", ""),
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE // 2,
        chunk_overlap=CHUNK_OVERLAP // 2,
        include_title=True,
    )
    chunks_df = pd.DataFrame(chunks)
    return _persist_chunks("schedule", DATASET_ARTIFACTS["schedule"].collection, chunks_df)


def ingest_courses() -> Tuple[pd.DataFrame, object, object]:
    paths = [DATA_SOURCES["courses_desc"], DATA_SOURCES["courses_major"]]
    frames: List[pd.DataFrame] = []
    for key, path in zip(["description", "major"], paths):
        if not path.exists():
            continue
        df = pd.read_csv(path).fillna("").astype(str)
        df["_source_table"] = key
        frames.append(df)

    if not frames:
        raise FileNotFoundError("Course CSV files are missing. Run the statistics crawler first.")

    combined = pd.concat(frames, ignore_index=True)

    docs: List[dict] = []
    ignored_exact = {"_source_table"}
    title_candidates = ["국문교과목명", "과목명", "course_name", "교과목명"]
    for _, row in combined.iterrows():
        title = next((str(row.get(col, "")).strip() for col in title_candidates if str(row.get(col, "")).strip()), "통계학과 교과")
        code = str(row.get("학수번호", "")).strip()
        doc_id = make_doc_id("courses", code or title, row.get("_source_table"))

        text_parts: List[str] = []
        for col, value in row.items():
            if col in ignored_exact or col.startswith("Unnamed"):
                continue
            value_str = str(value).strip()
            if not value_str:
                continue
            if col in title_candidates:
                text_parts.append(value_str)
            else:
                text_parts.append(f"{col}: {value_str}")
        text = "\n".join(text_parts).strip()
        if not text:
            continue
        docs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text": text,
                "course_code": code,
                "source_table": row.get("_source_table", ""),
                "topics": row.get("_source_table", ""),
                "source": "courses",
                "url": "",
                "published_at": "",
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=None,  # 대부분 짧은 설명이므로 통으로 사용
        chunk_overlap=0,
        include_title=True,
    )
    chunks_df = pd.DataFrame(chunks)
    return _persist_chunks("courses", DATASET_ARTIFACTS["courses"].collection, chunks_df)


def ingest_all() -> Dict[str, Tuple[pd.DataFrame, object, object]]:
    results: Dict[str, Tuple[pd.DataFrame, object, object]] = {}
    results["notices"] = ingest_notices()
    results["rules"] = ingest_rules()
    results["schedule"] = ingest_schedule()
    results["courses"] = ingest_courses()
    return results


def main() -> None:
    artifacts = ingest_all()
    for key, (chunks_df, _, _) in artifacts.items():
        print(f"✅ {key}: {len(chunks_df)} chunks indexed")


if __name__ == "__main__":
    main()


__all__ = [
    "DATASET_ARTIFACTS",
    "build_notice_chunks",
    "ingest_notices",
    "ingest_rules",
    "ingest_schedule",
    "ingest_courses",
    "ingest_all",
]
