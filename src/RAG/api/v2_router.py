"""동똑이 2.0 (v2) FastAPI 라우터 모듈 (Phase 4 - API Integration)

FastAPI 애플리케이션에 연결되어 v2 Multi-Agent Chat 및 Obsidian Graph API 엔드포인트를 제공합니다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.v2.agent_workflow import run_agentic_rag_v2
from src.v2.graph_store import get_graph_db_connection, search_entity_graph
from src.v2.obsidian_exporter import DEFAULT_VAULT_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["Dongttok 2.0 Engine"])


class ChatRequestV2(BaseModel):
    question: str


class ChatResponseV2(BaseModel):
    question: str
    category: str
    answer: str
    is_grounded: bool
    execution_trace: List[str]
    fts_docs_count: int
    graph_nodes_count: int
    latency_ms: float


class GraphResponseV2(BaseModel):
    keyword: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]


class VaultStatsResponseV2(BaseModel):
    status: str
    total_documents: int
    vault_path: str


@router.post("/chat", response_model=ChatResponseV2)
async def chat_v2(req: ChatRequestV2) -> ChatResponseV2:
    """Multi-Agent & Self-RAG 자율 대화 엔드포인트"""
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="질문 내용이 비어있습니다.")

    try:
        res = run_agentic_rag_v2(req.question.strip())
        return ChatResponseV2(**res)
    except Exception as e:
        logger.error(f"v2 Chat 엔드포인트 처리 중 오류 발생: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"v2 엔진 처리 실패: {str(e)}")


@router.get("/graph", response_model=GraphResponseV2)
async def get_entity_graph(
    keyword: str = Query(..., description="탐색할 학과, 과목, 학칙 또는 개념 키워드"),
    max_hops: int = Query(2, ge=1, le=3, description="지식 탐색 깊이 (1~3 Hops)")
) -> GraphResponseV2:
    """옵시디언 스타일 2D/3D 지식 그래프 노드 및 간선 조회 API"""
    try:
        conn = get_graph_db_connection()
        subgraph = search_entity_graph(conn, keyword=keyword.strip(), max_hops=max_hops)
        conn.close()
        return GraphResponseV2(
            keyword=keyword,
            nodes=subgraph.get("nodes", []),
            edges=subgraph.get("edges", [])
        )
    except Exception as e:
        logger.error(f"v2 Graph 엔드포인트 처리 중 오류 발생: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"지식 그래프 탐색 실패: {str(e)}")


@router.get("/vault/stats", response_model=VaultStatsResponseV2)
async def get_vault_stats() -> VaultStatsResponseV2:
    """생성된 Obsidian Vault 마크다운 지식 보관소 상태 조회 API"""
    try:
        if DEFAULT_VAULT_DIR.exists():
            md_files = list(DEFAULT_VAULT_DIR.rglob("*.md"))
            return VaultStatsResponseV2(
                status="active",
                total_documents=len(md_files),
                vault_path=str(DEFAULT_VAULT_DIR)
            )
        return VaultStatsResponseV2(
            status="not_generated",
            total_documents=0,
            vault_path=str(DEFAULT_VAULT_DIR)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
