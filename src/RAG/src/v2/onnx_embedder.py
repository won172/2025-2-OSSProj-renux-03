"""ONNX Runtime 기반 초고속 임베딩 추론 모듈 (v2)

PyTorch의 무거운 추론 오버헤드를 제거하고, ONNX Runtime (CPU/INT8 양자화)을 활용하여
질의 및 문서 임베딩 벡터를 10ms 이하의 소요 시간으로 생성합니다.
"""
from __future__ import annotations

import os
from pathlib import Path

# 오프라인 로컬 캐시 우선 사용 설정
hf_cache_dir = Path(__file__).resolve().parents[2] / "huggingface_cache"
if hf_cache_dir.exists():
    os.environ["HF_HOME"] = str(hf_cache_dir)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import logging
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# ONNX 모델 저장 경로 설정
MODEL_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_ONNX_PATH = MODEL_DIR / "kure_v1_quantized.onnx"

_ONNX_SESSION = None
_TOKENIZER = None


def resolve_model_path(model_name: str = "nlpai-lab/KURE-v1") -> str:
    """로컬 huggingface_cache 디렉터리에서 오프라인 스냅샷 경로를 자동으로 탐색"""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    
    cache_base = Path(__file__).resolve().parents[2] / "huggingface_cache"
    if cache_base.exists():
        os.environ["HF_HOME"] = str(cache_base)
        
    formatted_name = f"models--{model_name.replace('/', '--')}"
    snapshots_dir = cache_base / "hub" / formatted_name / "snapshots"
    
    if snapshots_dir.exists():
        snapshots = [p for p in snapshots_dir.iterdir() if p.is_dir()]
        if snapshots:
            logger.info(f"로컬 HF 스냅샷 발견: {snapshots[0]}")
            return str(snapshots[0])
            
    return model_name


def export_kure_to_onnx(
    output_path: Optional[Path] = None
) -> Path:
    """SentenceTransformer에서 모델 및 토크나이저를 추출하여 ONNX format으로 변환"""
    target_path = output_path or DEFAULT_ONNX_PATH
    if target_path.exists():
        logger.info(f"기존 ONNX 모델 존재: {target_path}")
        return target_path

    logger.info("SentenceTransformer ➔ ONNX 변환 시작...")
    import torch
    from sentence_transformers import SentenceTransformer

    resolved_path = resolve_model_path()
    embedder = SentenceTransformer(resolved_path)
    model = embedder[0].auto_model
    tokenizer = embedder.tokenizer
    model.eval()

    dummy_text = "동국대학교 학사 행정 안내"
    inputs = tokenizer(dummy_text, return_tensors="pt")
    
    input_names = ["input_ids", "attention_mask"]
    dynamic_axes = {
        "input_ids": {0: "batch_size", 1: "sequence_length"},
        "attention_mask": {0: "batch_size", 1: "sequence_length"},
        "last_hidden_state": {0: "batch_size", 1: "sequence_length"},
    }

    with torch.no_grad():
        torch.onnx.export(
            model,
            (inputs["input_ids"], inputs["attention_mask"]),
            str(target_path),
            input_names=input_names,
            output_names=["last_hidden_state"],
            dynamic_axes=dynamic_axes,
            opset_version=14,
            do_constant_folding=True,
        )

    # INT8 Dynamic Quantization 시도 (onnx 패키지 이용)
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
        quant_path = target_path.with_name("kure_v1_quantized.onnx")
        logger.info("ONNX 모델 INT8 동적 양자화 수행 중...")
        quantize_dynamic(
            model_input=str(target_path),
            model_output=str(quant_path),
            weight_type=QuantType.QUInt8,
        )
        if quant_path.exists():
            target_path = quant_path
    except Exception as exc:
        logger.info(f"INT8 양자화 생략 (FP32 ONNX 사용): {exc}")

    logger.info(f"✅ ONNX 모델 저장 완료: {target_path}")
    return target_path


def get_onnx_embedder(
    model_name: str = "nlpai-lab/KURE-v1",
    onnx_path: Optional[Path] = None
):
    """ONNX Runtime InferenceSession 및 Tokenizer 로드 (동적 폴백 포함)"""
    global _ONNX_SESSION, _TOKENIZER
    if _ONNX_SESSION is not None and _TOKENIZER is not None:
        return _ONNX_SESSION, _TOKENIZER

    import onnxruntime as ort
    from transformers import AutoTokenizer

    target_onnx = onnx_path or DEFAULT_ONNX_PATH
    
    # ONNX 모델이 없을 경우 자동 내보내기 시도
    if not target_onnx.exists():
        try:
            export_kure_to_onnx(target_onnx)
        except Exception as e:
            logger.warning(f"ONNX 변환 실패 ({e}).")
            
    resolved_path = resolve_model_path(model_name)
    from transformers import AutoTokenizer
    _TOKENIZER = AutoTokenizer.from_pretrained(resolved_path)

    if target_onnx.exists():
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session_options.intra_op_num_threads = min(4, os.cpu_count() or 1)
        _ONNX_SESSION = ort.InferenceSession(str(target_onnx), session_options, providers=["CPUExecutionProvider"])
        logger.info(f"ONNX Runtime 추론 세션 로드 성공: {target_onnx}")
    else:
        _ONNX_SESSION = None

    return _ONNX_SESSION, _TOKENIZER


def _mean_pooling(model_output: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Last hidden state에서 Attention Mask 기반 Mean Pooling 수행"""
    input_mask_expanded = np.expand_dims(attention_mask, -1).astype(np.float32)
    sum_embeddings = np.sum(model_output * input_mask_expanded, axis=1)
    sum_mask = np.clip(sum_embeddings * 0 + np.sum(input_mask_expanded, axis=1), a_min=1e-9, a_max=None)
    return sum_embeddings / sum_mask


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """L2 정규화 (코사인 유사도 내적 계산용)"""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def encode_texts_onnx(
    texts: Iterable[str],
    model_name: str = "nlpai-lab/KURE-v1",
    normalize: bool = True
) -> np.ndarray:
    """ONNX Runtime을 사용한 고성능 텍스트 밀집 벡터 변환"""
    text_list = [t if isinstance(t, str) else str(t) for t in texts]
    if not text_list:
        return np.empty((0, 768), dtype=np.float32)

    session, tokenizer = get_onnx_embedder(model_name)

    # ONNX 세션이 준비되지 않았을 때는 기본 SentenceTransformers 폴백
    if session is None:
        from src.models.embedding import encode_texts
        return encode_texts(text_list, normalize=normalize)

    # 토큰화
    inputs = tokenizer(
        text_list,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="np"
    )

    onnx_inputs = {
        "input_ids": inputs["input_ids"].astype(np.int64),
        "attention_mask": inputs["attention_mask"].astype(np.int64),
    }

    # ONNX 세션 추론
    outputs = session.run(["last_hidden_state"], onnx_inputs)
    last_hidden_state = outputs[0]

    # Mean Pooling & L2 Normalization
    embeddings = _mean_pooling(last_hidden_state, inputs["attention_mask"])
    if normalize:
        embeddings = _l2_normalize(embeddings)

    return embeddings.astype(np.float32)


@lru_cache(maxsize=512)
def _encode_single_query_onnx(query: str, model_name: str) -> Tuple[float, ...]:
    vec = encode_texts_onnx([query], model_name=model_name, normalize=True)[0]
    return tuple(float(v) for v in vec)


def encode_queries_onnx(
    queries: Iterable[str],
    model_name: str = "nlpai-lab/KURE-v1"
) -> np.ndarray:
    """검색 질의 초고속 변환 (LRU 캐시 적용)"""
    query_list = [q if isinstance(q, str) else str(q) for q in queries]
    if not query_list:
        return np.empty((0, 768), dtype=np.float32)

    results = [_encode_single_query_onnx(q, model_name) for q in query_list]
    return np.array(results, dtype=np.float32)
