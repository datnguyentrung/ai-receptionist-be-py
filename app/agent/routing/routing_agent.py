"""LLM fallback router with Ingestion Sub-agent and domain AgentTools."""

from pathlib import Path

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from app.agent.adk.model_factory import create_model
from app.agent.domains.admission.agent import create_admission_agent
from app.agent.domains.center_info.agent import create_center_info_agent
from app.agent.domains.chatchit.agent import create_chatchit_agent
from app.agent.domains.ingestion.agent import create_ingestion_agent
from app.agent.domains.training.agent import create_training_agent
from app.agent.domains.tuition.agent import create_tuition_agent
from app.agent.routing.tools import ask_clarifying_question

ROOT_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "root_agent_prompt.md"
)


def create_llm_fallback_agent(
    sub_agents: list | None = None,
    tools: list | None = None,
) -> Agent:
    """Khởi tạo LLM fallback router với Ingestion Sub-agent và 5 domain AgentTools."""
    if sub_agents is None:
        sub_agents = [
            create_ingestion_agent(),
        ]
    if tools is None:
        tools = [
            ask_clarifying_question,
            AgentTool(agent=create_admission_agent()),
            AgentTool(agent=create_tuition_agent()),
            AgentTool(agent=create_training_agent()),
            AgentTool(agent=create_center_info_agent()),
            AgentTool(agent=create_chatchit_agent()),
        ]
    return Agent(
        name="llm_fallback_router",
        model=create_model(),
        description="LLM Router dự phòng khi Clef không thể phân loại.",
        instruction=ROOT_PROMPT_PATH.read_text(encoding="utf-8"),
        sub_agents=sub_agents,
        tools=tools,
    )


__all__ = ["create_llm_fallback_agent"]
