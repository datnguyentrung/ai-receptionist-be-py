"""Ingestion Agent definition and initialization."""

from pathlib import Path

from google.adk.agents import Agent
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types

from app.agent.adk.model_factory import create_model
from app.agent.domains.ingestion.context_filter import compact_ingestion_before_model
from app.agent.skills.local_skill_registry import LocalSkillRegistry
from app.agent.skills.root_prompt_renderer import render_root_agent_prompt
from app.agent.skills.skill_loader import (
    discover_skill_descriptors,
    discover_skill_tools,
)

BASE_DIR = Path(__file__).resolve().parents[2]
SKILLS_DIR = BASE_DIR / "skills"
INGESTION_PROMPT_PATH = (
    BASE_DIR / "prompts" / "ingestion" / "ingestion_agent_prompt.md"
)


def create_ingestion_agent() -> Agent:
    """Khởi tạo Agent quản lý workflow Ingestion."""
    descriptors = discover_skill_descriptors(SKILLS_DIR)

    if not descriptors:
        raise RuntimeError(f"No valid SKILL.md files were found under {SKILLS_DIR}.")

    additional_tools = discover_skill_tools(descriptors)
    registry = LocalSkillRegistry(descriptors)

    instruction = render_root_agent_prompt(
        prompt_path=INGESTION_PROMPT_PATH,
        skill_descriptors=descriptors,
    )

    skill_toolset = SkillToolset(
        skills=[],
        registry=registry,
        additional_tools=additional_tools,
    )

    return Agent(
        name="INGESTION_AGENT",
        model=create_model(),
        description=(
            "Agent quản lý và thực thi quy trình ingestion tài liệu, "
            "trích xuất tri thức, ontology và kho dữ liệu Taekwondo."
        ),
        instruction=instruction,
        tools=[skill_toolset],
        before_model_callback=compact_ingestion_before_model,
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.MEDIUM
            ),
        ),
    )


__all__ = ["create_ingestion_agent"]
