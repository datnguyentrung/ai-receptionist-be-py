"""Capability Registry: Agent -> Skill -> Knowledge Scope -> Allowed Entity/Property/Relationship."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.ontology.seeds.layer2_core import LAYER_2_SCOPE_SPECS, TOOL_SCOPE_MAP


class SkillCapability(BaseModel, frozen=True):
    agent_key: str
    skill_key: str
    supported_scopes: tuple[str, ...]
    allowed_entities: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()


class CapabilityRegistry:
    """Shared registry validating agent/skill/scope access at publish and retrieval time."""

    def __init__(self, capabilities: tuple[SkillCapability, ...]) -> None:
        self._by_agent: dict[str, list[SkillCapability]] = {}
        self._by_scope: dict[str, set[str]] = {}
        for cap in capabilities:
            self._by_agent.setdefault(cap.agent_key, []).append(cap)
            for scope in cap.supported_scopes:
                self._by_scope.setdefault(scope, set()).update(cap.allowed_entities)

    @classmethod
    def default(cls) -> CapabilityRegistry:
        scope_entities = {k: tuple(v[1]) for k, v in LAYER_2_SCOPE_SPECS.items()}
        return cls(
            (
                SkillCapability(
                    agent_key="center_info",
                    skill_key="center_overview_qa",
                    supported_scopes=("center_overview",),
                    allowed_entities=scope_entities["center_overview"],
                    tool_names=(
                        "search_center_knowledge",
                        "search_opening_hours",
                        "search_center_facilities",
                        "search_contact_information",
                    ),
                ),
                SkillCapability(
                    agent_key="admission",
                    skill_key="admission_advisory",
                    supported_scopes=("admission_onboarding",),
                    allowed_entities=scope_entities["admission_onboarding"],
                    tool_names=(
                        "search_trial_policy",
                        "recommend_class",
                        "search_registration_process",
                    ),
                ),
                SkillCapability(
                    agent_key="training",
                    skill_key="training_curriculum_qa",
                    supported_scopes=("training_knowledge", "belt_and_exam"),
                    allowed_entities=(
                        *scope_entities["training_knowledge"],
                        *scope_entities["belt_and_exam"],
                    ),
                    tool_names=(
                        "search_training_knowledge",
                        "search_training_benefits",
                        "search_belt_system",
                        "search_exam_policy",
                        "search_training_policy",
                        "search_training_curriculum",
                    ),
                ),
                SkillCapability(
                    agent_key="tuition",
                    skill_key="tuition_advisory",
                    supported_scopes=("tuition_and_discount",),
                    allowed_entities=scope_entities["tuition_and_discount"],
                    tool_names=(
                        "search_tuition_policy",
                        "search_discount_policy",
                        "search_payment_policy",
                        "calculate_tuition",
                    ),
                ),
            )
        )

    def is_scope_supported(self, scope_key: str) -> bool:
        return scope_key.strip().casefold() in self._by_scope

    def is_entity_allowed_in_scope(self, scope_key: str, entity_type: str) -> bool:
        allowed = self._by_scope.get(scope_key.strip().casefold())
        if not allowed:
            return False
        return entity_type.strip().casefold() in {e.casefold() for e in allowed}

    def is_agent_allowed_scope(self, agent_key: str, scope_key: str) -> bool:
        caps = self._by_agent.get(agent_key.strip().casefold(), [])
        norm_scope = scope_key.strip().casefold()
        return any(norm_scope in cap.supported_scopes for cap in caps)

    def scope_for_tool(self, tool_name: str) -> str | None:
        return TOOL_SCOPE_MAP.get(tool_name)


__all__ = ["CapabilityRegistry", "SkillCapability"]
