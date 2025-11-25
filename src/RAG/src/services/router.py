"LLM을 사용하여 사용자의 질문을 가장 관련 있는 데이터셋으로 라우팅합니다."
from __future__ import annotations

from typing import List

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

from src.config import LLM_ROUTER_DESCRIPTIONS, OPENAI_MODEL

# LLM이 출력할 라우팅 결정의 스키마를 정의합니다.
# 여러 데이터셋과 관련될 수 있으므로 문자열 리스트를 사용합니다.
class RouteChoice(BaseModel):
    """사용자 질문에 가장 관련 있는 데이터셋(들)의 이름."""
    names: List[str] = Field(
        description="선택된 데이터셋의 이름 리스트. 예를 들어 ['rules', 'schedule']와 같이 출력되어야 합니다."
    )

# LangChain V2부터 PydanticOutputParser를 사용하는 것이 권장됩니다.
parser = PydanticOutputParser(pydantic_object=RouteChoice)

# LLM에게 제공할 프롬프트 템플릿입니다.
# 사용자의 질문과 각 데이터셋의 설명을 제공하고, 가장 적절한 것을 선택하도록 요청합니다.
prompt = PromptTemplate(
    template="""사용자의 질문을 분석하여 가장 관련 있는 데이터셋으로 라우팅하는 역할을 수행합니다.
질문에 답변하기 위해 참조해야 할 가장 적절한 데이터셋을 하나 이상 선택하세요.

사용 가능한 데이터셋:
{destinations}

사용자 질문:
{query}

선택된 데이터셋의 이름을 포함하는 JSON 객체를 다음 형식으로 출력하세요:
{format_instructions}
""",
    input_variables=["query", "destinations"],
    partial_variables={"format_instructions": parser.get_format_instructions()},
)

# 라우팅을 수행할 LLM 체인을 구성합니다.
llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0, model_kwargs={"response_format": {"type": "json_object"}})
router_chain = prompt | llm | parser

def _format_destinations() -> str:
    """LLM 프롬프트에 포함될 데이터셋 설명의 형식을 지정합니다."""
    return "\n".join(f"- {name}: {desc}" for name, desc in LLM_ROUTER_DESCRIPTIONS.items())

async def route_query(query: str) -> List[str]:
    """LLM 라우터를 사용하여 사용자 질문에 가장 적합한 데이터셋을 결정합니다."""
    if not query:
        # 질문이 비어 있으면 기본값으로 'notices'를 반환합니다.
        return ["notices"]

    formatted_destinations = _format_destinations()
    
    try:
        result = await router_chain.ainvoke({
            "query": query,
            "destinations": formatted_destinations,
        })
        # LLM의 선택에서 유효한 데이터셋 이름만 필터링합니다.
        # LLM이 목록에 없는 이름을 지어낼 경우를 대비합니다.
        valid_routes = [name for name in result.names if name in LLM_ROUTER_DESCRIPTIONS]
        return valid_routes or ["notices"]  # 유효한 선택이 없으면 기본값으로 'notices' 반환
    except Exception:
        # LLM 호출 실패 등 예외 발생 시 기본값으로 'notices'를 반환합니다.
        return ["notices"]

# 이 파일에서 외부에 제공할 함수는 route_query 뿐입니다.
__all__ = ["route_query"]