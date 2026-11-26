"""Shared factory for skill-backed domain AgentTools."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from google.adk import skills
from google.adk.agents import Agent
from google.adk.tools.skill_toolset import SkillToolset

from app.agent.adk.model_factory import create_model
from app.agent.skills.local_skill_registry import LocalSkillRegistry
from app.agent.skills.skill_loader import SkillDescriptor

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def build_domain_skill_toolset(
    skill_codes: Sequence[str],
    additional_tools: Sequence[Any] = (),
) -> SkillToolset:
    """Build a SkillToolset scoped strictly to one domain's skills and tools."""
    descriptors: list[SkillDescriptor] = []
    for code in skill_codes:
        skill_dir = SKILLS_ROOT / code
        loaded_skill = skills.load_skill_from_dir(skill_dir)
        descriptors.append(
            SkillDescriptor(
                code=code,
                directory=skill_dir,
                frontmatter=loaded_skill.frontmatter,
            )
        )

    return SkillToolset(
        registry=LocalSkillRegistry(descriptors),
        additional_tools=list(additional_tools),
    )


def create_skill_domain_agent(
    *,
    name: str,
    description: str,
    instruction: str,
    skill_codes: Sequence[str],
    additional_tools: Sequence[Any] = (),
) -> Agent:
    """Create an ADK Agent backed by a scoped SkillToolset."""
    toolset = build_domain_skill_toolset(
        skill_codes=skill_codes,
        additional_tools=additional_tools,
    )
    return Agent(
        name=name,
        model=create_model(),
        description=description,
        instruction=instruction,
        tools=[toolset],
    )


__all__ = ["build_domain_skill_toolset", "create_skill_domain_agent"]
