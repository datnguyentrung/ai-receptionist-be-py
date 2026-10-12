"""Unit tests for multi-domain routing architecture and domain agents."""

from types import SimpleNamespace

from google.genai import types

from app.agent.agent import root_agent
from app.agent.domains.calculate.agent import create_calculation_agent
from app.agent.domains.graph_qa.agent import create_graph_qa_agent
from app.agent.domains.ingestion.agent import create_ingestion_agent
from app.agent.domains.ingestion.context_filter import (
    compact_ingestion_before_model,
    compact_ingestion_context,
)
from app.agent.routing.tools import ask_clarifying_question
from app.agent.tools.calculation import add, divide, multiply, subtract


def test_calculation_tools():
    """Kiểm tra các công cụ tính toán số học."""
    assert add(15, 25)["result"] == 40
    assert subtract(100, 35)["result"] == 65
    assert multiply(12, 8)["result"] == 96
    assert divide(144, 12)["result"] == 12

    div_zero = divide(10, 0)
    assert div_zero["success"] is False
    assert div_zero["errorCode"] == "DIVISION_BY_ZERO"


def test_ask_clarifying_question_tool():
    """Kiểm tra tool ask_clarifying_question của root router."""
    result = ask_clarifying_question(
        question="Bạn muốn tra cứu học phí hay tính toán số tiền?",
        options=["Tra cứu học phí", "Tính toán"],
        ambiguity_type="domain_overlap",
        reasoning="Học phí có thể là tra cứu hoặc tính toán",
        candidate_agents=["GRAPH_QA_AGENT", "CALCULATION_AGENT"],
    )
    assert result["status"] == "clarification_needed"
    assert len(result["options"]) == 2
    assert "GRAPH_QA_AGENT" in result["candidate_agents"]


def test_root_router_structure():
    """Kiểm tra cấu trúc Root Router Agent."""
    assert root_agent.name == "root_agent"
    sub_agent_names = [a.name for a in root_agent.sub_agents]
    assert "INGESTION_AGENT" in sub_agent_names
    assert "CALCULATION_AGENT" in sub_agent_names
    assert "GRAPH_QA_AGENT" in sub_agent_names

    tool_names = [
        getattr(t, "name", None) or getattr(t, "__name__", None)
        for t in root_agent.tools
    ]
    assert "ask_clarifying_question" in tool_names


def test_calculation_agent_sequential_structure():
    """Kiểm tra CALCULATION_AGENT là SequentialAgent gồm tool agent và format agent."""
    calc_agent = create_calculation_agent()
    assert calc_agent.name == "CALCULATION_AGENT"
    assert len(calc_agent.sub_agents) == 2
    tool_agent = calc_agent.sub_agents[0]
    format_agent = calc_agent.sub_agents[1]

    assert tool_agent.name == "calculation_tool_agent"
    assert format_agent.name == "calculation_format_agent"

    tool_names = {getattr(t, "__name__", None) for t in tool_agent.tools}
    assert {"add", "subtract", "multiply", "divide"}.issubset(tool_names)


def test_graph_qa_agent_sequential_structure():
    """Kiểm tra GRAPH_QA_AGENT là SequentialAgent gồm tool agent và format agent."""
    gqa_agent = create_graph_qa_agent()
    assert gqa_agent.name == "GRAPH_QA_AGENT"
    assert len(gqa_agent.sub_agents) == 2
    tool_agent = gqa_agent.sub_agents[0]
    format_agent = gqa_agent.sub_agents[1]

    assert tool_agent.name == "graph_qa_tool_agent"
    assert format_agent.name == "graph_qa_format_agent"

    tool_names = {getattr(t, "__name__", None) for t in tool_agent.tools}
    assert {
        "execute_read_cypher",
        "vector_search",
        "hybrid_search",
        "retrieve_taekwondo_knowledge",
        "retrieve_taekwondo_document_history",
        "list_active_schema_scopes",
        "load_active_schema_context",
    }.issubset(tool_names)


def test_ingestion_agent_structure():
    """Kiểm tra INGESTION_AGENT giữ nguyên cơ chế SkillToolset và callback."""
    ing_agent = create_ingestion_agent()
    assert ing_agent.name == "INGESTION_AGENT"
    assert len(ing_agent.tools) == 1
    assert type(ing_agent.tools[0]).__name__ == "SkillToolset"
    assert ing_agent.before_model_callback == compact_ingestion_before_model


def test_compact_ingestion_before_model():
    """Kiểm tra compact_ingestion_before_model lọc context cho Ingestion."""
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text="Nạp tài liệu")],
        ),
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="load_skill", args={}))],
        ),
        types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(name="load_skill", response={}))],
        ),
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="ingestion_batch_agent", args={}))],
        ),
        types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(name="ingestion_batch_agent", response={}))],
        ),
        types.Content(
            role="model",
            parts=[types.Part(text="Hoàn tất batch 1")],
        ),
    ]

    llm_request = SimpleNamespace(contents=contents)
    compact_ingestion_before_model(SimpleNamespace(), llm_request)
    assert len(llm_request.contents) > 0
