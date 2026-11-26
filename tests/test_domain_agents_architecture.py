"""Unit tests for multi-domain routing architecture and domain agents."""

from types import SimpleNamespace

import pytest
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types

from app.agent.agent import root_agent
from app.agent.domains.admission.agent import ADMISSION_SKILLS, create_admission_agent
from app.agent.domains.calculate.agent import create_calculation_agent
from app.agent.domains.center_info.agent import CENTER_INFO_SKILLS, create_center_info_agent
from app.agent.domains.chatchit.agent import CHATCHIT_SKILLS, create_chatchit_agent
from app.agent.domains.graph_qa.agent import create_graph_qa_agent
from app.agent.domains.ingestion.agent import create_ingestion_agent
from app.agent.domains.training.agent import TRAINING_SKILLS, create_training_agent
from app.agent.domains.tuition.agent import TUITION_SKILLS, create_tuition_agent
from app.agent.domains.ingestion.context_filter import (
    compact_ingestion_before_model,
)
from app.agent.routing.clef_router import VALID_ROUTES
from app.agent.routing.hybrid_root_agent import DIRECT_ROUTES
from app.agent.routing.routing_agent import create_llm_fallback_agent
from app.agent.routing.tools import ask_clarifying_question
from app.agent.tools.admission_tools import (
    recommend_class,
    search_admission_knowledge,
    search_class_knowledge,
    search_registration_process,
    search_trial_policy,
)
from app.agent.tools.calculation import add, divide, multiply, subtract
from app.agent.tools.center_info_tools import (
    search_center_facilities,
    search_center_knowledge,
    search_center_locations,
    search_coach_information,
    search_contact_information,
    search_opening_hours,
)
from app.agent.tools.training_tools import (
    search_belt_system,
    search_class_details,
    search_class_schedule,
    search_exam_policy,
    search_training_benefits,
    search_training_curriculum,
    search_training_knowledge,
    search_training_policy,
)
from app.agent.tools.tuition_tools import (
    calculate_tuition,
    search_additional_fees,
    search_discount_policy,
    search_payment_policy,
    search_tuition_packages,
    search_tuition_policy,
)


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
    """Root chỉ chứa LLM fallback; Clef dispatch trực tiếp qua fallback này."""
    assert root_agent.name == "root_agent"
    sub_agent_names = [a.name for a in root_agent.sub_agents]
    assert sub_agent_names == ["llm_fallback_router"]


def test_chatchit_agent_is_agent_tool_not_sub_agent():
    """CHATCHIT_AGENT đã chuyển từ sub-agent sang AgentTool với 3 skill giao tiếp."""
    agent = create_chatchit_agent()
    assert agent.name == "CHATCHIT_AGENT"
    assert isinstance(agent.tools[0], SkillToolset)
    assert "CHATCHIT_AGENT" in VALID_ROUTES
    assert "CHATCHIT_AGENT" in DIRECT_ROUTES

    fallback = create_llm_fallback_agent()
    sub_agent_names = {sub_agent.name for sub_agent in fallback.sub_agents}
    assert "CHATCHIT_AGENT" not in sub_agent_names
    assert "INGESTION_AGENT" in sub_agent_names

    agent_tool_names = {
        tool.agent.name
        for tool in fallback.tools
        if isinstance(tool, AgentTool)
    }
    assert {
        "ADMISSION_AGENT",
        "TUITION_AGENT",
        "TRAINING_AGENT",
        "CENTER_INFO_AGENT",
        "CHATCHIT_AGENT",
    } == agent_tool_names


def test_clef_hybrid_root_resolves_agent_tools_and_sub_agents():
    """HybridRootAgent phân giải được cả Sub-agent lẫn AgentTool cho CLEF direct dispatch."""
    expected_routes = {
        "INGESTION_AGENT",
        "ADMISSION_AGENT",
        "TUITION_AGENT",
        "TRAINING_AGENT",
        "CENTER_INFO_AGENT",
        "CHATCHIT_AGENT",
    }
    assert expected_routes == DIRECT_ROUTES
    assert expected_routes.union({"ASK_CLARIFICATION"}) == VALID_ROUTES

    for route in expected_routes:
        resolved = root_agent._resolve_target_agent(route)
        assert resolved is not None
        assert resolved.name == route


def test_domain_agent_tools_skills_and_stub_functions():
    """Kiểm tra 5 AgentTools nạp đúng SkillToolset và các FunctionTool stub."""
    cases = [
        (
            create_admission_agent(),
            ADMISSION_SKILLS,
            {
                "search_admission_knowledge",
                "search_class_knowledge",
                "recommend_class",
                "search_trial_policy",
                "search_registration_process",
            },
        ),
        (
            create_tuition_agent(),
            TUITION_SKILLS,
            {
                "search_tuition_policy",
                "search_tuition_packages",
                "search_additional_fees",
                "search_discount_policy",
                "calculate_tuition",
                "search_payment_policy",
            },
        ),
        (
            create_training_agent(),
            TRAINING_SKILLS,
            {
                "search_class_schedule",
                "search_class_details",
                "search_training_knowledge",
                "search_training_curriculum",
                "search_training_benefits",
                "search_belt_system",
                "search_exam_policy",
                "search_training_policy",
            },
        ),
        (
            create_center_info_agent(),
            CENTER_INFO_SKILLS,
            {
                "search_center_knowledge",
                "search_center_locations",
                "search_opening_hours",
                "search_coach_information",
                "search_center_facilities",
                "search_contact_information",
            },
        ),
        (
            create_chatchit_agent(),
            CHATCHIT_SKILLS,
            {"search_contact_information"},
        ),
    ]

    for agent, expected_skills, expected_tools in cases:
        assert len(agent.tools) == 1
        toolset = agent.tools[0]
        assert isinstance(toolset, SkillToolset)
        assert set(toolset._registry._descriptors.keys()) == set(expected_skills)
        assert set(toolset._provided_tools_by_name.keys()) == expected_tools

    # FunctionTools not backed by a Java API in this rollout remain stubs.
    stub_calls = [
        lambda: search_trial_policy("học thử"),
        lambda: search_tuition_policy("bảo lưu"),
        lambda: search_discount_policy("ưu đãi"),
        lambda: calculate_tuition("gói cơ bản"),
        lambda: search_payment_policy("chuyển khoản"),
        lambda: search_training_knowledge("bài quyền"),
        lambda: search_training_benefits("lợi ích"),
        lambda: search_belt_system("hệ thống đai"),
        lambda: search_exam_policy("thi lên đai"),
        lambda: search_training_policy("học bù"),
        lambda: search_center_knowledge("lịch sử"),
        lambda: search_opening_hours("giờ mở cửa"),
        lambda: search_center_facilities("cơ sở vật chất"),
    ]
    for call in stub_calls:
        with pytest.raises(NotImplementedError):
            call()


def test_calculation_agent_sequential_structure():
    """Kiểm tra CALCULATION_AGENT là SequentialAgent gồm Tool Agent và Format Agent."""
    calc_agent = create_calculation_agent()
    assert calc_agent.name == "CALCULATION_AGENT"
    assert len(calc_agent.sub_agents) == 2
    tool_agent = calc_agent.sub_agents[0]
    format_agent = calc_agent.sub_agents[1]

    assert tool_agent.name == "calculation_tool_agent"
    assert format_agent.name == "calculation_format_agent"

    # Tool Agent được gắn trực tiếp 4 calculation tools
    tool_names = {getattr(t, "__name__", None) for t in tool_agent.tools}
    assert {"add", "subtract", "multiply", "divide"}.issubset(tool_names)

    # Format Agent không gắn function tools
    assert format_agent.tools == [] or format_agent.tools is None


def test_graph_qa_agent_sequential_structure():
    """Graph QA retrieves deterministically before one formatting LLM call."""
    gqa_agent = create_graph_qa_agent()
    assert gqa_agent.name == "GRAPH_QA_AGENT"
    assert len(gqa_agent.sub_agents) == 2
    retrieval_agent = gqa_agent.sub_agents[0]
    format_agent = gqa_agent.sub_agents[1]

    assert retrieval_agent.name == "graph_qa_retrieval"
    assert retrieval_agent.has_model is False
    assert getattr(retrieval_agent, "tools", []) in ([], None)
    assert format_agent.name == "graph_qa_format_agent"
    assert format_agent.tools == [] or format_agent.tools is None
    assert format_agent.include_contents == "none"


def test_ingestion_agent_structure():
    """INGESTION_AGENT chỉ exposes tool ingestion đã được nạp trực tiếp."""
    ing_agent = create_ingestion_agent()
    assert ing_agent.name == "INGESTION_AGENT"
    tool_names = {
        getattr(tool, "name", getattr(tool, "__name__", None))
        for tool in ing_agent.tools
    }
    assert "ingestion_batch_agent" in tool_names
    assert "load_skill" not in tool_names
    assert "load_active_schema_context" not in tool_names
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
            parts=[types.Part(function_call=types.FunctionCall(name="begin_ingestion", args={}))],
        ),
        types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(name="begin_ingestion", response={}))],
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
