"""LLM fallback router."""

from pathlib import Path

from google.adk.agents import Agent

from app.agent.adk.model_factory import create_model
from app.agent.domains.calculate.agent import create_calculation_agent
from app.agent.domains.graph_qa.agent import create_graph_qa_agent
from app.agent.domains.ingestion.agent import create_ingestion_agent
from app.agent.routing.tools import ask_clarifying_question

ROOT_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "root_agent_prompt.md"
)


def create_llm_fallback_agent(sub_agents: list | None = None) -> Agent:
    if sub_agents is None:
        sub_agents = [
            create_ingestion_agent(),
            create_calculation_agent(),
            create_graph_qa_agent(),
        ]
    return Agent(
        name="llm_fallback_router",
        model=create_model(),
        description="LLM Router dự phòng khi Clef không thể phân loại.",
        instruction=ROOT_PROMPT_PATH.read_text(encoding="utf-8"),
        sub_agents=sub_agents,
        tools=[ask_clarifying_question],
    )
