import argparse
import asyncio
import json
import os
import pandas as pd
import sys
from pathlib import Path
from typing import List
import uuid

# 프로젝트 루트를 path에 추가
sys.path.append(str(Path(__file__).resolve().parents[1]))
from fastapi import Request
from src.database import SessionLocal, RagQueryLog


# ---------- LLM-judge 채점 ----------
# expected_answer 컬럼이 있으면 정답 기준으로, 없으면 출처-답변 근거 일치성 기준으로 채점한다.
JUDGE_SYSTEM_PROMPT = """당신은 대학 챗봇 답변을 채점하는 평가자입니다.
질문, 챗봇 답변, 검색된 출처(있다면), 기대 정답(있다면)을 보고 JSON으로 채점하세요.

채점 기준:
- verdict: "correct"(질문에 정확히 답함) / "partial"(핵심 요소 일부가 빠짐) / "wrong"(틀리거나 동문서답)
  - 기대 정답이 있으면 그것과의 사실 일치로 판단하세요.
  - 기대 정답이 없으면 답변이 질문에 유용하게 응답했는지로 판단하세요.
  - 챗봇이 "자료에서 확인되지 않는다"고 정직하게 답한 경우, 출처가 실제로 무관하면 correct로 취급하세요.
  - 인사·잡담 질문은 자연스럽게 응대했으면 correct입니다(감정 표현의 구체성 등은 따지지 마세요).
  - partial은 **질문이 요구한 핵심 정보(날짜/번호/절차 등)가 답변에 빠진 경우에만** 주세요.
    문체, 표현의 모호함, 출처 표기 형식, 부가 정보 부족 같은 사유로 깎지 마세요.
  - 동일한 답변은 항상 동일한 verdict가 나오도록, 위 기준에 기계적으로 따르세요.
- grounded: 답변의 핵심 주장들이 제공된 출처 내용으로 뒷받침되면 true, 출처에 없는 내용을 단정하면 false.
  잡담 등 출처가 필요 없는 답변은 true로 두세요.
- reason: 한 문장 근거.

JSON만 출력: {"verdict": "...", "grounded": true/false, "reason": "..."}"""


DEFAULT_THRESHOLDS = {
    "route_hit_rate": 0.80,
    "context_recall_proxy": 0.70,
    "answer_relevancy_proxy": 0.70,
    "fallback_rate": 0.35,
}


def _mean_or_none(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.mean())


def _bool_mean_or_none(series: pd.Series) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.astype(bool).mean())


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def summarize_results(results_df: pd.DataFrame, thresholds: dict[str, float]) -> dict:
    """RAGAS류 지표에 대응하는 최소 운영 지표를 계산한다.

    - faithfulness_proxy: grounding 또는 judge 기반 근거 일치율
    - context_recall_proxy: 기대 데이터셋이 실제 출처에 포함된 비율
    - answer_relevancy_proxy: 평가셋 키워드 포함 점수 평균
    """
    summary: dict = {
        "total_questions": int(len(results_df)),
        "thresholds": thresholds,
        "metrics": {},
        "by_dataset": {},
        "warnings": [],
    }
    if results_df.empty:
        summary["warnings"].append("evaluation_set is empty")
        return summary

    metrics = summary["metrics"]
    if "hit" in results_df.columns:
        metrics["route_hit_rate"] = _bool_mean_or_none(results_df["hit"])
    if "context_recall_proxy" in results_df.columns:
        metrics["context_recall_proxy"] = _bool_mean_or_none(results_df["context_recall_proxy"])
    if "keyword_score" in results_df.columns:
        metrics["answer_relevancy_proxy"] = _mean_or_none(results_df["keyword_score"])
    if "fallback" in results_df.columns:
        metrics["fallback_rate"] = _bool_mean_or_none(results_df["fallback"])
    if "judge_grounded" in results_df.columns and results_df["judge_grounded"].notna().any():
        metrics["faithfulness_proxy"] = _bool_mean_or_none(results_df["judge_grounded"])
    elif "grounding_grounded" in results_df.columns:
        metrics["faithfulness_proxy"] = _bool_mean_or_none(results_df["grounding_grounded"])
    if "grounding_score" in results_df.columns:
        metrics["avg_grounding_score"] = _mean_or_none(results_df["grounding_score"])
    if "top_hybrid_score" in results_df.columns:
        metrics["avg_top_hybrid_score"] = _mean_or_none(results_df["top_hybrid_score"])

    for dataset, group in results_df.groupby("expected_dataset", dropna=False):
        dataset_key = str(dataset)
        summary["by_dataset"][dataset_key] = {
            "count": int(len(group)),
            "route_hit_rate": _bool_mean_or_none(group["hit"]) if "hit" in group else None,
            "context_recall_proxy": _bool_mean_or_none(group["context_recall_proxy"]) if "context_recall_proxy" in group else None,
            "answer_relevancy_proxy": _mean_or_none(group["keyword_score"]) if "keyword_score" in group else None,
            "fallback_rate": _bool_mean_or_none(group["fallback"]) if "fallback" in group else None,
        }

    for metric_name, threshold in thresholds.items():
        value = metrics.get(metric_name)
        if value is None:
            continue
        if metric_name == "fallback_rate":
            if value > threshold:
                summary["warnings"].append(f"{metric_name} {_pct(value)} exceeds threshold {_pct(threshold)}")
        elif value < threshold:
            summary["warnings"].append(f"{metric_name} {_pct(value)} below threshold {_pct(threshold)}")

    return summary


def write_markdown_report(summary: dict, results_df: pd.DataFrame, output_path: Path) -> None:
    metrics = summary.get("metrics", {})
    lines = [
        "# RAG Evaluation Report",
        "",
        f"- Total questions: {summary.get('total_questions', 0)}",
        f"- Route hit rate: {_pct(metrics.get('route_hit_rate'))}",
        f"- Context recall proxy: {_pct(metrics.get('context_recall_proxy'))}",
        f"- Answer relevancy proxy: {_pct(metrics.get('answer_relevancy_proxy'))}",
        f"- Faithfulness proxy: {_pct(metrics.get('faithfulness_proxy'))}",
        f"- Fallback rate: {_pct(metrics.get('fallback_rate'))}",
        "",
        "## Dataset Summary",
        "",
        "| Dataset | Count | Route Hit | Context Recall | Answer Relevancy | Fallback |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for dataset, row in summary.get("by_dataset", {}).items():
        lines.append(
            f"| {dataset} | {row.get('count', 0)} | {_pct(row.get('route_hit_rate'))} | "
            f"{_pct(row.get('context_recall_proxy'))} | {_pct(row.get('answer_relevancy_proxy'))} | "
            f"{_pct(row.get('fallback_rate'))} |"
        )

    warnings = summary.get("warnings", [])
    lines.extend(["", "## Gate Warnings", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None")

    if not results_df.empty and "hit" in results_df.columns:
        misses = results_df[(results_df["hit"] == False) | (results_df.get("context_recall_proxy", True) == False)]  # noqa: E712
        if not misses.empty:
            lines.extend(["", "## Miss Samples", ""])
            for _, row in misses.head(10).iterrows():
                lines.append(
                    f"- `{row.get('expected_dataset', '')}` {row.get('question', '')} "
                    f"(route={row.get('actual_route', '')}, sources={row.get('source_datasets', '')}, "
                    f"fallback={row.get('fallback', '')})"
                )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def judge_answer(question: str, answer: str, sources_text: str, expected_answer: str = "") -> dict:
    """OpenAI로 답변을 채점합니다. 실패 시 빈 dict."""
    import json as _json
    try:
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_MODEL

        llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0, timeout=30, max_retries=1,
                         model_kwargs={"response_format": {"type": "json_object"}})
        user_msg = f"[질문]\n{question}\n\n[챗봇 답변]\n{answer}\n\n[검색된 출처]\n{sources_text or '(없음)'}"
        if expected_answer:
            user_msg += f"\n\n[기대 정답]\n{expected_answer}"
        resp = await llm.ainvoke([
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        data = _json.loads(resp.content)
        verdict = data.get("verdict")
        if verdict not in ("correct", "partial", "wrong"):
            return {}
        return {
            "judge_verdict": verdict,
            "judge_grounded": bool(data.get("grounded", False)),
            "judge_reason": str(data.get("reason", ""))[:300],
        }
    except Exception as e:  # noqa: BLE001
        print(f"   - ⚠️ Judge failed: {e}")
        return {}


async def run_evaluation(
    csv_path: str,
    use_query_analysis: bool,
    use_judge: bool = False,
    output_dir: str | None = None,
    thresholds: dict[str, float] | None = None,
    fail_on_threshold: bool = False,
):
    os.environ["RAG_USE_QUERY_ANALYSIS"] = "1" if use_query_analysis else "0"
    from api.rag_service import AskRequest, ask, _is_recent_notice_query

    thresholds = thresholds or DEFAULT_THRESHOLDS
    df = pd.read_csv(csv_path)
    results = []
    
    print(f"🚀 Starting RAG Evaluation with {len(df)} questions...")
    
    # Mock FastAPI Request for request_id
    class MockRequest:
        def __init__(self, request_id: str):
            self.state = type('state', (), {'request_id': request_id})()
    
    for _, row in df.iterrows():
        question = row['question']
        expected_ds = row['expected_dataset']
        raw_keywords = row.get("expected_keywords")
        if pd.isna(raw_keywords):
            keywords: List[str] = []
        else:
            keywords = [k.strip() for k in str(raw_keywords).split(',') if k.strip()]
        request_id = f"eval_{uuid.uuid4().hex}"
        
        print(f"🧐 Evaluating: {question}")
        
        req = AskRequest(question=question)
        try:
            mock_request = MockRequest(request_id)
            resp = await ask(req, mock_request)
            
            # 1. Route Check (Hit Rate)
            # 잡담 분기는 route=["unknown"]으로 표현된다(검색 생략 + 폴백 없음이면 정상 처리).
            smalltalk_route_hit = expected_ds == "smalltalk" and (
                resp.route in (["smalltalk"], ["unknown"]) and not resp.fallback_triggered
            )
            hit = smalltalk_route_hit if expected_ds == "smalltalk" else expected_ds in resp.route
            
            # 2. Answer Quality Check (Keyword Containment)
            keyword_hits = [k for k in keywords if k in resp.answer]
            keyword_score = len(keyword_hits) / len(keywords) if keywords else 1.0
            
            # 3. Fallback Check
            fallback = resp.fallback_triggered
            recent_notice_query = _is_recent_notice_query(question, resp.route)
            source_count = len(resp.sources)
            top_source = resp.sources[0] if resp.sources else None
            source_datasets = sorted(
                {
                    str(getattr(source, "source", "") or "").strip()
                    for source in (resp.sources or [])
                    if str(getattr(source, "source", "") or "").strip()
                }
            )
            context_recall_proxy = (
                expected_ds == "smalltalk"
                or expected_ds in source_datasets
            )

            session = SessionLocal()
            try:
                query_log = (
                    session.query(RagQueryLog)
                    .filter(RagQueryLog.request_id == request_id)
                    .order_by(RagQueryLog.created_at.desc())
                    .first()
                )
            finally:
                session.close()
            
            judge_result: dict = {}
            if use_judge:
                sources_text = "\n".join(
                    f"- {getattr(s, 'title', '')} ({getattr(s, 'published_at', '')})"
                    for s in (resp.sources or [])
                )
                expected_answer = str(row.get("expected_answer", "") or "")
                if expected_answer.lower() == "nan":
                    expected_answer = ""
                judge_result = await judge_answer(question, resp.answer, sources_text, expected_answer)

            results.append({
                "question": question,
                "expected_dataset": expected_ds,
                "actual_route": ", ".join(resp.route),
                **judge_result,
                "hit": hit,
                "smalltalk_route_hit": smalltalk_route_hit,
                "keyword_score": keyword_score,
                "fallback": fallback,
                "fallback_reason": resp.fallback_reason,
                "source_count": source_count,
                "source_datasets": ", ".join(source_datasets),
                "context_recall_proxy": context_recall_proxy,
                "top_hybrid_score": None if query_log is None else query_log.top_hybrid_score,
                "final_score": None if top_source is None else top_source.final_score,
                "grounding_checked": None if query_log is None else query_log.grounding_checked,
                "grounding_grounded": None if query_log is None else query_log.grounding_grounded,
                "grounding_score": None if query_log is None else query_log.grounding_score,
                "date_filter_relaxed": None if query_log is None else query_log.date_filter_relaxed,
                "recent_notice_query": recent_notice_query,
                "analysis_intent": None if query_log is None else query_log.analysis_intent,
                "analysis_used": None if query_log is None else query_log.analysis_used,
                "analysis_failed": None if query_log is None else query_log.analysis_failed,
                "analysis_needs_clarification": None if query_log is None else query_log.analysis_needs_clarification,
                "matched_queries_json": None if query_log is None else query_log.matched_queries_json,
            })
            
            print(
                "   - Hit: "
                f"{'✅' if hit else '❌'} | Score: {keyword_score:.2f} | "
                f"Context: {'✅' if context_recall_proxy else '❌'} | "
                f"Fallback: {fallback} ({resp.fallback_reason}) | Sources: {source_count}"
            )
            
        except Exception as e:
            print(f"   - 💥 Error: {e}")
            results.append({
                "question": question,
                "error": str(e)
            })

    results_df = pd.DataFrame(results)
    
    print("\n" + "="*30)
    print("📊 Evaluation Summary")
    print("="*30)
    print(f"Total Questions: {len(results_df)}")
    if "hit" in results_df.columns:
        print(f"Query Analysis Enabled: {use_query_analysis}")
        print(f"Average Hit Rate (Dataset Matching): {results_df['hit'].mean():.2%}")
        print(f"Average Keyword Score: {results_df['keyword_score'].mean():.2%}")
        print(f"Fallback Frequency: {results_df['fallback'].mean():.2%}")
        dataset_hit_rate = results_df.groupby("expected_dataset")["hit"].mean().sort_values(ascending=False)
        print("Dataset Hit Rate:")
        print(dataset_hit_rate.to_string())
        if "fallback_reason" in results_df.columns:
            print("Fallback Reason Distribution:")
            print(results_df["fallback_reason"].fillna("none").value_counts().to_string())
        if "recent_notice_query" in results_df.columns:
            recent_queries = results_df[results_df["recent_notice_query"] == True]
            if not recent_queries.empty:
                print(f"Recent Query Failure Rate: {recent_queries['fallback'].mean():.2%}")
        if "analysis_used" in results_df.columns:
            valid_analysis = results_df["analysis_used"].dropna()
            if not valid_analysis.empty:
                print(f"Query Analysis Success Rate: {valid_analysis.mean():.2%}")
        if "analysis_needs_clarification" in results_df.columns:
            ambiguous = results_df[results_df["analysis_needs_clarification"] == True]
            if not ambiguous.empty:
                print(f"Clarification-needed Query Ratio: {len(ambiguous) / len(results_df):.2%}")
        misses = results_df[results_df["hit"] == False]
        if not misses.empty:
            print("Route Miss Top Question Types:")
            print(misses["expected_dataset"].value_counts().to_string())
        if "judge_verdict" in results_df.columns:
            judged = results_df["judge_verdict"].dropna()
            if not judged.empty:
                print("\n🧑‍⚖️ LLM Judge:")
                print(f"  Correct: {(judged == 'correct').mean():.2%} | Partial: {(judged == 'partial').mean():.2%} | Wrong: {(judged == 'wrong').mean():.2%}")
                grounded = results_df["judge_grounded"].dropna()
                if not grounded.empty:
                    print(f"  Grounded (출처 근거 일치): {grounded.mean():.2%}")
    
    summary = summarize_results(results_df, thresholds)

    timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
    output_root = Path(output_dir) if output_dir else Path(csv_path).parent
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"evaluation_results_{timestamp}.csv"
    summary_path = output_root / f"evaluation_summary_{timestamp}.json"
    report_path = output_root / f"evaluation_report_{timestamp}.md"

    results_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(summary, results_df, report_path)

    print(f"\n✅ Detailed results saved to: {output_path}")
    print(f"✅ Summary saved to: {summary_path}")
    print(f"✅ Markdown report saved to: {report_path}")
    if summary["warnings"]:
        print("\n⚠️ Gate warnings:")
        for warning in summary["warnings"]:
            print(f"   - {warning}")
        if fail_on_threshold:
            raise SystemExit(2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable-analysis", action="store_true")
    parser.add_argument(
        "--eval-set",
        default=str(Path(__file__).resolve().parents[1] / "tests" / "evaluation_set.csv"),
        help="평가셋 CSV 경로 (columns: question, expected_dataset, expected_keywords[, expected_answer])",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="결과 CSV/JSON/Markdown 리포트를 저장할 디렉터리 (기본: 평가셋 디렉터리)",
    )
    parser.add_argument("--judge", action="store_true",
                        help="LLM-judge로 답변 정확도/근거 일치성 채점 (OPENAI_API_KEY 필요). "
                             "evaluation_set.csv에 expected_answer 컬럼이 있으면 정답 기준으로 채점")
    parser.add_argument("--min-route-hit", type=float, default=DEFAULT_THRESHOLDS["route_hit_rate"])
    parser.add_argument("--min-context-recall", type=float, default=DEFAULT_THRESHOLDS["context_recall_proxy"])
    parser.add_argument("--min-answer-relevancy", type=float, default=DEFAULT_THRESHOLDS["answer_relevancy_proxy"])
    parser.add_argument("--max-fallback-rate", type=float, default=DEFAULT_THRESHOLDS["fallback_rate"])
    parser.add_argument(
        "--fail-on-threshold",
        action="store_true",
        help="게이트 기준 미달/초과 경고가 있으면 종료 코드 2로 실패 처리",
    )
    args = parser.parse_args()
    gate_thresholds = {
        "route_hit_rate": args.min_route_hit,
        "context_recall_proxy": args.min_context_recall,
        "answer_relevancy_proxy": args.min_answer_relevancy,
        "fallback_rate": args.max_fallback_rate,
    }
    asyncio.run(
        run_evaluation(
            str(args.eval_set),
            use_query_analysis=not args.disable_analysis,
            use_judge=args.judge,
            output_dir=args.output_dir,
            thresholds=gate_thresholds,
            fail_on_threshold=args.fail_on_threshold,
        )
    )
