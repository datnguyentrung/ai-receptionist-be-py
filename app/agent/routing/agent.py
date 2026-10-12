"""Root Routing Agent definition."""

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


def create_root_agent() -> Agent:
    """Khởi tạo Root Routing Agent điều phối tới các domain chuyên biệt."""
    return Agent(
        name="root_agent",
        model=create_model(),
        description=(
            "Root Router điều phối yêu cầu đến các Agent nghiệp vụ chuyên biệt: "
            "INGESTION_AGENT, CALCULATION_AGENT, GRAPH_QA_AGENT."
        ),
        instruction=ROOT_PROMPT_PATH.read_text(encoding="utf-8"),
        sub_agents=[
            create_ingestion_agent(),
            create_calculation_agent(),
            create_graph_qa_agent(),
        ],
        tools=[
            ask_clarifying_question,
        ],
    )


__all__ = ["create_root_agent"]
