import asyncio
import os
import json
import pandas as pd
from typing import List, Dict
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

# RAG 서비스 import (로컬 실행 가정)
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

# 필요한 모듈 import (ingest 등 초기화 필요)
from src.database import init_db
from api.rag_service import ask, AskRequest, bootstrap_artifacts

# 평가용 LLM 설정
EVAL_MODEL = "gpt-4o-mini" # 비용 절감을 위해 mini 사용, 더 정확한 평가는 gpt-4 권장

# 평가 결과 스키마
class EvalResult(BaseModel):
    score: int = Field(description="1 to 5 score")
    reasoning: str = Field(description="Reasoning for the score")

parser = JsonOutputParser(pydantic_object=EvalResult)

# 평가 프롬프트 템플릿
CORRECTNESS_PROMPT = PromptTemplate(
    template="""당신은 RAG 시스템의 답변 품질을 평가하는 채점관입니다.
    
[질문]: {question}
[기준 답변]: {ground_truth}
[생성된 답변]: {generated_answer}

생성된 답변이 기준 답변의 핵심 정보를 정확하게 포함하고 있는지 평가하세요.
1점(전혀 다름)부터 5점(완벽하게 일치)까지 점수를 매기고 이유를 설명하세요.

{format_instructions}
""",
    input_variables=["question", "ground_truth", "generated_answer"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)

FAITHFULNESS_PROMPT = PromptTemplate(
    template="""당신은 RAG 시스템의 답변이 검색된 문서에 기반했는지 평가하는 채점관입니다.

[검색된 문맥]:
{context}

[생성된 답변]:
{generated_answer}

생성된 답변이 오직 제공된 문맥에만 기반하여 작성되었는지, 문맥에 없는 내용을 지어내지(Hallucination) 않았는지 평가하세요.
1점(문맥과 전혀 상관없음/허위정보)부터 5점(문맥에 완벽히 기반함)까지 점수를 매기고 이유를 설명하세요.

{format_instructions}
""",
    input_variables=["context", "generated_answer"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)

async def evaluate_single(item: Dict, llm: ChatOpenAI):
    question = item["question"]
    ground_truth = item["ground_truth"]
    
    print(f"🔍 Evaluating: {question}")
    
    # 1. RAG 실행
    try:
        response = await ask(AskRequest(question=question))
        generated_answer = response.answer
        # 검색된 문맥 조합
        context = "\n".join([f"- {src.snippet}" for src in response.sources])
        
        if not context:
            context = "검색된 문서 없음"
            
    except Exception as e:
        print(f"❌ Error during RAG generation: {e}")
        return None

    # 2. LLM 평가 (Correctness)
    try:
        correctness_chain = CORRECTNESS_PROMPT | llm | parser
        correctness_result = await correctness_chain.ainvoke({
            "question": question,
            "ground_truth": ground_truth,
            "generated_answer": generated_answer
        })
    except Exception as e:
        print(f"⚠️ Correctness eval failed: {e}")
        correctness_result = {"score": 0, "reasoning": "Eval Failed"}
    
    # 3. LLM 평가 (Faithfulness)
    try:
        faithfulness_chain = FAITHFULNESS_PROMPT | llm | parser
        faithfulness_result = await faithfulness_chain.ainvoke({
            "context": context[:10000], # 토큰 제한 고려하여 자름
            "generated_answer": generated_answer
        })
    except Exception as e:
        print(f"⚠️ Faithfulness eval failed: {e}")
        faithfulness_result = {"score": 0, "reasoning": "Eval Failed"}

    return {
        "question": question,
        "generated_answer": generated_answer,
        "ground_truth": ground_truth,
        "correctness_score": correctness_result.get("score", 0),
        "correctness_reason": correctness_result.get("reasoning", ""),
        "faithfulness_score": faithfulness_result.get("score", 0),
        "faithfulness_reason": faithfulness_result.get("reasoning", ""),
        "retrieved_docs_count": len(response.sources)
    }

async def main():
    # 초기화
    init_db()
    bootstrap_artifacts()

    # 1. 테스트 데이터셋 (실제 데이터 기반 질문으로 재구성)
    test_dataset = [
        # --- 학사일정 (Schedule - dongguk_schedule.csv 기반) ---
        {
            "question": "2025학년도 1학기 개강일은 언제야?",
            "ground_truth": "2025년 3월 4일입니다."
        },
        {
            "question": "2025년 1학기 수강신청 확인 및 정정 기간 알려줘",
            "ground_truth": "2025년 3월 4일부터 3월 10일까지입니다."
        },
        {
            "question": "여름방학(하계방학) 시작일은 언제야?",
            "ground_truth": "2025년 6월 23일입니다."
        },
        {
            "question": "2025년 부처님오신날은 언제야? 수업 해?",
            "ground_truth": "2025년 5월 5일이며, 공휴일이므로 수업이 없습니다. (보강일 지정 가능성 있음)"
        },

        # --- 학칙 (Rules - dongguk_rule_texts.csv 기반) ---
        {
            "question": "일반 휴학은 최대 몇 년까지 할 수 있어?",
            "ground_truth": "일반휴학 기간은 1회에 1년(2개 학기) 이내로 하며, 재학 중 통산 3년(6개 학기)을 초과할 수 없습니다."
        },
        {
            "question": "조기졸업 하려면 성적이 얼마나 되어야 해?",
            "ground_truth": "6학기 또는 7학기 이수 후 졸업요건을 갖추고, 총 평점평균이 4.0 이상이어야 조기졸업이 가능합니다."
        },
        {
            "question": "성적경고(학사경고) 기준이 뭐야?",
            "ground_truth": "매 학기 성적 평점평균이 1.75 미만인 경우 성적경고를 받습니다."
        },
        {
            "question": "전과(소속변경) 신청 자격은 어떻게 돼?",
            "ground_truth": "2학년 또는 3학년 진급 예정자로서, 총 평점평균 3.0 이상이어야 신청 가능합니다."
        },

        # --- 교과목 (Courses - 통계학과 데이터 기반) ---
        {
            "question": "'탐색적자료분석' 과목의 학수번호 알려줘",
            "ground_truth": "STA2005 입니다."
        },
        {
            "question": "수리통계학1 수업은 몇 학점이야?",
            "ground_truth": "3학점입니다."
        },
        {
            "question": "통계학과 2학년이 들을만한 전공 기초 과목 추천해줘",
            "ground_truth": "탐색적자료분석(STA2005), 확률과정론(STA2015), 수리통계학1(STA2017) 등이 2학년 대상 기초 과목입니다."
        },
        {
            "question": "'회귀해석' 과목은 영어로 수업해?",
            "ground_truth": "네, 원어강의(영어)로 진행되는 과목입니다."
        },
        {
            "question": "수리통계학2의 선수과목이 있어?",
            "ground_truth": "대학통계및실습1, 대학통계및실습2, 수리통계학1 이 선수권장 과목입니다."
        },

        # --- 교직원/부서 (Staff - dongguk_staff_contacts.csv 기반) ---
        {
            "question": "학사지원팀 전화번호가 뭐야?",
            "ground_truth": "054-770-2033 (또는 검색된 학사지원팀 번호)"
        },
        {
            "question": "장학팀 위치 알려줘",
            "ground_truth": "본관 1층 등 (데이터에 위치 정보가 있다면)"
        },
        {
            "question": "학생상담센터에서는 무슨 일을 해?",
            "ground_truth": "학생들의 심리 상담, 진로 상담 등의 업무를 담당합니다."
        },

        # --- 공지사항 (Notices - 최근 공지 기반) ---
        {
            "question": "2025학년도 신입생 등록금 납부 기간은?",
            "ground_truth": "2025년 2월 중 지정된 기간 (공지사항 내용 참조)"
        },
        {
            "question": "졸업앨범 촬영 일정 나왔어?",
            "ground_truth": "공지사항에 졸업앨범 촬영 관련 안내가 있다면 해당 날짜와 장소를 안내합니다."
        },

        # --- 일상 대화 (Chit-chat) ---
        {
            "question": "안녕, 너는 누구니?",
            "ground_truth": "저는 동국대학교 재학생을 위한 맞춤형 정보 제공 챗봇 '동똑이'입니다."
        },
        {
            "question": "반가워",
            "ground_truth": "반갑습니다! 학교 생활에 대해 궁금한 점이 있으신가요?"
        }
    ]

    llm = ChatOpenAI(model=EVAL_MODEL, temperature=0)
    
    results = []
    for item in test_dataset:
        result = await evaluate_single(item, llm)
        if result:
            results.append(result)
    
    # 결과 출력 및 저장
    if results:
        df = pd.DataFrame(results)
        print("\n📊 Evaluation Results:")
        print(df[["question", "correctness_score", "faithfulness_score"]])
        print(f"\nAverage Correctness: {df['correctness_score'].mean():.2f}")
        print(f"Average Faithfulness: {df['faithfulness_score'].mean():.2f}")
        
        output_file = "rag_evaluation_report.csv"
        df.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"\n✅ 상세 리포트 저장됨: {output_file}")
    else:
        print("평가 결과가 없습니다.")

if __name__ == "__main__":
    asyncio.run(main())
