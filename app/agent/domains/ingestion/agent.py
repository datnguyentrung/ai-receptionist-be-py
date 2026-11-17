"""Ingestion Agent definition and initialization."""

from pathlib import Path

from google.adk.agents import Agent
from google.genai import types

from app.agent.adk.model_factory import create_model
from app.agent.domains.ingestion.context_filter import compact_ingestion_before_model
from app.agent.skills.ingestion.ingestion import build_skill
from app.agent.skills.ingestion.tools import get_tools

BASE_DIR = Path(__file__).resolve().parents[2]
INGESTION_PROMPT_PATH = (
    BASE_DIR / "prompts" / "ingestion" / "ingestion_agent_prompt.md"
)


def create_ingestion_agent() -> Agent:
    """Khởi tạo Agent quản lý workflow Ingestion."""
    root_instruction = INGESTION_PROMPT_PATH.read_text(encoding="utf-8").strip()
    instruction = f"{root_instruction}\n\n{build_skill().instructions}"

    return Agent(
        name="INGESTION_AGENT",
        model=create_model(),
        description=(
            "Agent quản lý và thực thi quy trình ingestion tài liệu, "
            "trích xuất tri thức, ontology và kho dữ liệu Taekwondo."
        ),
        instruction=instruction,
        tools=get_tools(),
        before_model_callback=compact_ingestion_before_model,
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.MEDIUM
            ),
        ),
    )


__all__ = ["create_ingestion_agent"]
