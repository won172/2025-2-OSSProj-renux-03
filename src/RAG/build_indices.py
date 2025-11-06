# build_indices.py
import pandas as pd
import re
from typing import List, Dict
from settings import DATA, DB_DIR, EMBED_MODEL, CHUNK_SIZE, CHUNK_OVERLAP
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from pathlib import Path
from langchain_huggingface import HuggingFaceEmbeddings  # KURE-v1 사용

EMBEDDINGS = HuggingFaceEmbeddings(
    model_name="nlpai-lab/KURE-v1",
    model_kwargs={"device": "cpu"},          # "cuda" 또는 "mps" 가능
    encode_kwargs={"normalize_embeddings": True, "batch_size": 8}
)

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())

def chunk_text(text: str, size: int, overlap: int) -> List[str]:
    # 간단 토큰 유사 분할(문자 기반). 필요 시 tiktoken/KoNLPy 기반으로 개선.
    text = normalize_whitespace(text)
    if not text:
        return []
    chunks, step = [], max(1, size - overlap)
    for i in range(0, len(text), step):
        chunk = text[i:i+size]
        chunks.append(chunk)
        if i + size >= len(text):
            break
    return chunks

def make_docs_from_frame(
    df: pd.DataFrame,
    text_cols: List[str],
    meta_cols: List[str],
    source: str,
    chunk_long_text: bool = False
) -> List[Document]:
    docs: List[Document] = []
    for _, row in df.iterrows():
        # 본문 결합
        body = " ".join([normalize_whitespace(str(row.get(c, ""))) for c in text_cols if str(row.get(c, "")).strip()])
        if not body:
            continue

        metadata = {c: str(row.get(c, "")).strip() for c in meta_cols}
        metadata["source"] = source

        if chunk_long_text:
            for ch in chunk_text(body, CHUNK_SIZE, CHUNK_OVERLAP):
                docs.append(Document(page_content=ch, metadata=metadata))
        else:
            docs.append(Document(page_content=body, metadata=metadata))
    return docs

def index_collection(name: str, docs: List[Document], persist_dir: Path):
    print(f"[*] Indexing collection: {name} ({len(docs)} docs)")
    vectordb = Chroma(
        collection_name=name,
        embedding_function=EMBEDDINGS,
        persist_directory=str(persist_dir)
    )
    if len(docs) > 0:
        vectordb.add_documents(docs)
        print(f"[+] Collection '{name}' saved to {persist_dir}")
    print(f"[+] Done: {name}")

def build_notices():
    df = pd.read_csv(DATA["notices"])
    # 컬럼 예측(없으면 건너뜀)
    text_cols = [c for c in ["title", "body", "content"] if c in df.columns] or [df.columns[-1]]
    meta_cols = [c for c in ["category","department","published_at","valid_from","valid_to","url"] if c in df.columns]
    docs = make_docs_from_frame(df, text_cols, meta_cols, "notices", chunk_long_text=False)
    index_collection("notices", docs, DB_DIR)

def build_rules():
    df = pd.read_csv(DATA["rules"])
    text_cols = [c for c in ["title","body","content","article"] if c in df.columns] or [df.columns[-1]]
    meta_cols = [c for c in ["rule_name","chapter","section","article_no","url","updated_at","valid_from","valid_to"] if c in df.columns]
    docs = make_docs_from_frame(df, text_cols, meta_cols, "rules", chunk_long_text=True)
    index_collection("rules", docs, DB_DIR)

def build_schedule():
    df = pd.read_csv(DATA["schedule"])
    text_cols = [c for c in ["title","desc","description","memo"] if c in df.columns] or [df.columns[-1]]
    meta_cols = [c for c in ["start_date","end_date","owner","department","url","published_at"] if c in df.columns]
    docs = make_docs_from_frame(df, text_cols, meta_cols, "schedule", chunk_long_text=False)
    index_collection("schedule", docs, DB_DIR)

def build_courses():
    # 두 CSV를 합쳐 courses 컬렉션으로 인덱싱
    frames = []
    for key in ["courses_desc","courses_major"]:
        df = pd.read_csv(DATA[key])
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    text_cols = [c for c in ["course_title","course_name","description","syllabus","summary"] if c in df.columns] or [df.columns[-1]]
    meta_cols = [c for c in ["department","major","credit","category","prerequisite","semester","url"] if c in df.columns]
    docs = make_docs_from_frame(df, text_cols, meta_cols, "courses", chunk_long_text=False)
    index_collection("courses", docs, DB_DIR)

if __name__ == "__main__":
    build_notices()
    build_rules()
    build_schedule()
    build_courses()
    print("[✓] All collections built.")
