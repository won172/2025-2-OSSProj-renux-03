import argparse
import asyncio
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


async def run_evaluation(csv_path: str, use_query_analysis: bool):
    os.environ["RAG_USE_QUERY_ANALYSIS"] = "1" if use_query_analysis else "0"
    from api.rag_service import AskRequest, ask, _is_recent_notice_query

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
            smalltalk_route_hit = expected_ds == "smalltalk" and resp.route == ["smalltalk"]
            hit = smalltalk_route_hit if expected_ds == "smalltalk" else expected_ds in resp.route
            
            # 2. Answer Quality Check (Keyword Containment)
            keyword_hits = [k for k in keywords if k in resp.answer]
            keyword_score = len(keyword_hits) / len(keywords) if keywords else 1.0
            
            # 3. Fallback Check
            fallback = resp.fallback_triggered
            recent_notice_query = _is_recent_notice_query(question, resp.route)
            source_count = len(resp.sources)
            top_source = resp.sources[0] if resp.sources else None

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
            
            results.append({
                "question": question,
                "expected_dataset": expected_ds,
                "actual_route": ", ".join(resp.route),
                "hit": hit,
                "smalltalk_route_hit": smalltalk_route_hit,
                "keyword_score": keyword_score,
                "fallback": fallback,
                "fallback_reason": resp.fallback_reason,
                "source_count": source_count,
                "top_hybrid_score": None if query_log is None else query_log.top_hybrid_score,
                "final_score": None if top_source is None else top_source.final_score,
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
    
    output_path = Path(csv_path).parent / f"evaluation_results_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
    results_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n✅ Detailed results saved to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable-analysis", action="store_true")
    args = parser.parse_args()
    eval_set_path = Path(__file__).resolve().parents[1] / "tests" / "evaluation_set.csv"
    asyncio.run(run_evaluation(str(eval_set_path), use_query_analysis=not args.disable_analysis))
