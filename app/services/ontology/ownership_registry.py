"""Data Ownership Registry and schema/predicate-level governance rules across Layers 1, 2, and 3.

Single deep module used by seeds, OntologyCompiler, ingestion validators,
Layer 3 schema proposal guards, and retrieval gates.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceOfTruth(StrEnum):
    BUSINESS_API = "BUSINESS_API"
    CORE_KNOWLEDGE = "CORE_KNOWLEDGE"
    TENANT_EXTENSION = "TENANT_EXTENSION"


class IngestionMode(StrEnum):
    DENY = "DENY"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    ALLOW = "ALLOW"
    STRUCTURED_ONLY = "STRUCTURED_ONLY"


class ExtensionMode(StrEnum):
    LOCKED = "LOCKED"
    EXTENSIBLE_WITH_REVIEW = "EXTENSIBLE_WITH_REVIEW"
    TENANT_EXTENSIBLE = "TENANT_EXTENSIBLE"


class VisibilityLevel(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    RESTRICTED = "RESTRICTED"


class OwnershipVerdict(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class FactType(StrEnum):
    PROPERTY = "PROPERTY"
    RELATIONSHIP = "RELATIONSHIP"
    BUSINESS_REF = "BUSINESS_REF"


class GovernanceMetadata(BaseModel):
    """Canonical ownership & governance contract (section 5.1)."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    layer: str = "LAYER_2_CORE"
    source_of_truth: SourceOfTruth = Field(alias="sourceOfTruth")
    ingestion_mode: IngestionMode = Field(alias="ingestionMode")
    extension_mode: ExtensionMode = Field(alias="extensionMode")
    visibility: VisibilityLevel = VisibilityLevel.PUBLIC
    owner_agent_keys: tuple[str, ...] = Field(default=(), alias="ownerAgentKeys")
    review_required: bool = Field(default=True, alias="reviewRequired")
    source_binding: dict[str, Any] | None = Field(default=None, alias="sourceBinding")

    def to_compiled_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class OwnershipDecision(BaseModel):
    """Result of a schema & predicate-level ownership / governance check."""

    verdict: OwnershipVerdict = OwnershipVerdict.ALLOW
    allowed: bool = True
    code: str
    reason: str
    source_of_truth: SourceOfTruth | None = None
    ingestion_mode: IngestionMode | None = None

    @model_validator(mode="before")
    @classmethod
    def _sync_verdict_and_allowed(cls, data: Any) -> Any:
        if isinstance(data, dict):
            verdict = data.get("verdict")
            allowed = data.get("allowed")
            if verdict is not None and allowed is None:
                v = OwnershipVerdict(verdict)
                data["allowed"] = v == OwnershipVerdict.ALLOW
            elif allowed is not None and verdict is None:
                data["verdict"] = (
                    OwnershipVerdict.ALLOW if bool(allowed) else OwnershipVerdict.DENY
                )
        return data


def _normalize_token(value: str) -> str:
    """Normalize camelCase / snake_case / kebab-case into lowercase alphanumeric."""
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    return re.sub(r"[^a-z0-9]+", "_", s1.casefold()).strip("_")


# Canonical operational predicates owned by BUSINESS_API that Layer 2/3 facts or proposals must not shadow.
_BUSINESS_API_PROTECTED_PROPERTY_TOKENS: dict[str, str] = {
    "start_time": "class_schedule.start_time",
    "end_time": "class_schedule.end_time",
    "class_start_time": "class_schedule.start_time",
    "class_end_time": "class_schedule.end_time",
    "schedule_time": "class_schedule.start_time",
    "weekday": "class_schedule.weekday",
    "session_date": "class_session.session_date",
    "base_price": "training_plan.base_price",
    "final_price": "training_plan.final_price",
    "tuition_fee": "training_plan.final_price",
    "package_price": "training_plan.final_price",
    "price": "training_plan_addon.price",
    "address": "branch.address",
    "branch_address": "branch.address",
    "hotline": "branch.hotline",
    "hotline_number": "branch.hotline",
    "phone_number": "branch.hotline",
    "capacity": "course.capacity",
    "max_capacity": "course.capacity",
    "course_status": "course.status",
    "branch_status": "branch.status",
    "exam_result": "belt_exam.result",
    "belt_exam_result": "belt_exam.result",
    "student_belt_exam": "belt_exam",
    "attendance_status": "class_session.attendance_status",
    "attendance_record": "class_session.attendance_status",
    "check_in_time": "class_session.check_in_time",
    "debt_amount": "training_plan.tuition_debt",
    "tuition_debt": "training_plan.tuition_debt",
    "payment_status": "training_plan.payment_status",
    "invoice_amount": "training_plan.invoice_amount",
}

# Deterministic FORM type -> (canonical entity_type, allowed scope_keys)
_FORM_TYPE_COMPATIBILITY: dict[str, tuple[str, tuple[str, ...]]] = {
    "center-profile": ("center_profile", ("center_overview",)),
    "facility-item": ("facility_item", ("center_overview",)),
    "policy-rule": (
        "policy_rule",
        (
            "center_overview",
            "admission_onboarding",
            "training_knowledge",
            "belt_and_exam",
            "tuition_and_discount",
        ),
    ),
    "class-suitability": (
        "class_suitability_profile",
        ("admission_onboarding", "training_knowledge"),
    ),
    "enrollment-step": ("enrollment_step", ("admission_onboarding",)),
    "taekwondo-concept": ("taekwondo_concept", ("training_knowledge", "belt_and_exam")),
    "poomsae": ("poomsae", ("belt_and_exam", "training_knowledge")),
    "belt-rank": ("belt_rank", ("belt_and_exam",)),
    "discount-rule": ("discount_rule", ("tuition_and_discount",)),
}


class OwnershipRegistry:
    """Unified schema & predicate-level lookup & governance validator for Layers 1, 2, and approved Layer 3."""

    def __init__(
        self,
        *,
        entities: dict[str, GovernanceMetadata] | None = None,
        properties: dict[tuple[str, str], GovernanceMetadata] | None = None,
        relationships: dict[tuple[str, str, str], GovernanceMetadata] | None = None,
        layer2_aliases: dict[str, tuple[str, ...]] | None = None,
        scope_entities: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._entities: dict[str, GovernanceMetadata] = entities or {}
        self._properties: dict[tuple[str, str], GovernanceMetadata] = properties or {}
        self._relationships: dict[tuple[str, str, str], GovernanceMetadata] = relationships or {}
        self._layer2_aliases: dict[str, tuple[str, ...]] = layer2_aliases or {}
        self._scope_entities: dict[str, tuple[str, ...]] = scope_entities or {}

    @classmethod
    def from_seeds(cls) -> OwnershipRegistry:
        """Build the canonical Layer 1 + Layer 2 registry directly from seed definitions."""
        from app.services.ontology.seeds.layer1_business import (
            LAYER_1_ENTITY_TYPES,
            LAYER_1_PROPERTIES,
            LAYER_1_RELATIONSHIPS,
            get_layer1_entity_governance,
            get_layer1_property_governance,
            get_layer1_relationship_governance,
        )
        from app.services.ontology.seeds.layer2_core import (
            LAYER_2_ALIASES,
            LAYER_2_ENTITY_TYPES,
            LAYER_2_PROPERTIES,
            LAYER_2_RELATIONSHIPS,
            LAYER_2_SCOPE_SPECS,
            get_layer2_entity_governance,
            get_layer2_property_governance,
            get_layer2_relationship_governance,
        )

        entities: dict[str, GovernanceMetadata] = {}
        properties: dict[tuple[str, str], GovernanceMetadata] = {}
        relationships: dict[tuple[str, str, str], GovernanceMetadata] = {}

        for spec in LAYER_1_ENTITY_TYPES:
            name = str(spec["technical_name"]).casefold()
            entities[name] = get_layer1_entity_governance(spec)
        for entity_name, prop_specs in LAYER_1_PROPERTIES.items():
            ename = entity_name.casefold()
            for prop in prop_specs:
                properties[(ename, prop[0].casefold())] = get_layer1_property_governance(
                    ename, prop[0]
                )
        for src, rel, tgt, *_ in LAYER_1_RELATIONSHIPS:
            relationships[(src.casefold(), rel.casefold(), tgt.casefold())] = (
                get_layer1_relationship_governance(src, rel, tgt)
            )

        for spec in LAYER_2_ENTITY_TYPES:
            name = str(spec["technical_name"]).casefold()
            entities[name] = get_layer2_entity_governance(spec)
        for entity_name, prop_specs in LAYER_2_PROPERTIES.items():
            ename = entity_name.casefold()
            for prop in prop_specs:
                properties[(ename, prop[0].casefold())] = get_layer2_property_governance(
                    ename, prop[0]
                )
        for src, rel, tgt, *_ in LAYER_2_RELATIONSHIPS:
            relationships[(src.casefold(), rel.casefold(), tgt.casefold())] = (
                get_layer2_relationship_governance(src, rel, tgt)
            )

        scope_entities = {
            scope_key.casefold(): tuple(e.casefold() for e in spec[1])
            for scope_key, spec in LAYER_2_SCOPE_SPECS.items()
        }

        return cls(
            entities=entities,
            properties=properties,
            relationships=relationships,
            layer2_aliases=LAYER_2_ALIASES,
            scope_entities=scope_entities,
        )

    @classmethod
    def from_compiled_snapshots(
        cls, snapshots: Sequence[dict[str, Any]]
    ) -> OwnershipRegistry:
        """Build a registry from compiled snapshot payloads (shared across runtime stages)."""
        entities: dict[str, GovernanceMetadata] = {}
        properties: dict[tuple[str, str], GovernanceMetadata] = {}
        relationships: dict[tuple[str, str, str], GovernanceMetadata] = {}

        for schema in snapshots:
            for item in schema.get("entityTypes", []):
                gov = item.get("governance")
                name = str(item.get("technicalName", "")).casefold()
                if name and isinstance(gov, dict):
                    entities[name] = GovernanceMetadata.model_validate(gov)
            for item in schema.get("properties", []):
                gov = item.get("governance")
                ename = str(item.get("entityType", "")).casefold()
                pname = str(item.get("technicalName", "")).casefold()
                if ename and pname and isinstance(gov, dict):
                    properties[(ename, pname)] = GovernanceMetadata.model_validate(gov)
            for item in schema.get("relationships", []):
                gov = item.get("governance")
                src = str(item.get("sourceEntityType", "")).casefold()
                rel = str(item.get("technicalName", "")).casefold()
                tgt = str(item.get("targetEntityType", "")).casefold()
                if src and rel and tgt and isinstance(gov, dict):
                    relationships[(src, rel, tgt)] = GovernanceMetadata.model_validate(gov)

        return cls(
            entities=entities,
            properties=properties,
            relationships=relationships,
        )

    def resolve_entity(self, entity_type: str) -> GovernanceMetadata | None:
        return self._entities.get(entity_type.strip().casefold())

    def resolve_property(
        self, entity_type: str, property_name: str
    ) -> GovernanceMetadata | None:
        return self._properties.get(
            (entity_type.strip().casefold(), property_name.strip().casefold())
        )

    def resolve_relationship(
        self, source_type: str, rel_name: str, target_type: str | None = None
    ) -> GovernanceMetadata | None:
        src = source_type.strip().casefold()
        rel = rel_name.strip().casefold()
        if target_type is not None:
            return self._relationships.get((src, rel, target_type.strip().casefold()))
        for (s, r, _), gov in self._relationships.items():
            if s == src and r == rel:
                return gov
        return None

    def check_fact_ingestion(
        self,
        entity_type: str,
        property_name: str | None = None,
        *,
        predicate: str | None = None,
        fact_type: str = FactType.PROPERTY.value,
        subject_key: str | None = None,
        source_type: str | None = None,
        form_type: str | None = None,
        scope_key: str | None = None,
        is_reference_only: bool = False,
        statement: str | None = None,  # Deprecated & ignored: never inspect free text for ownership
    ) -> OwnershipDecision:
        """Evaluate whether a structured fact (entity_type + predicate) may be ingested into the Knowledge Graph.

        Never inspects `statement` or freeform text. Ownership is strictly determined by schema & predicate metadata.
        """
        _ = (statement, subject_key)
        ename = (entity_type or "").strip().casefold()
        effective_predicate = (predicate or property_name or "").strip().casefold()
        normalized_fact_type = (fact_type or FactType.PROPERTY.value).strip().upper()
        normalized_source_type = (source_type or "").strip().upper() or None
        normalized_form_type = (form_type or "").strip().casefold() or None
        normalized_scope_key = (scope_key or "").strip().casefold() or None
        is_ref = is_reference_only or normalized_fact_type == FactType.BUSINESS_REF.value

        # 1. FORM source compatibility check (form_type <-> entity_type & scope_key)
        if normalized_form_type is not None:
            compat = _FORM_TYPE_COMPATIBILITY.get(normalized_form_type)
            if compat is None:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_UNKNOWN_FORM_TYPE",
                    reason=f"Form type '{normalized_form_type}' is not registered in the Ontology.",
                )
            expected_entity, allowed_scopes = compat
            if ename != expected_entity and not is_ref:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_FORM_ENTITY_MISMATCH",
                    reason=(
                        f"Form '{normalized_form_type}' maps to entity '{expected_entity}', "
                        f"cannot emit primary facts for entity '{ename}'."
                    ),
                )
            if normalized_scope_key and normalized_scope_key not in allowed_scopes:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_FORM_SCOPE_MISMATCH",
                    reason=(
                        f"Form '{normalized_form_type}' is not allowed in scope '{normalized_scope_key}'."
                    ),
                )

        # 2. Scope validity check (when scope_key is provided, it must be a registered scope)
        if normalized_scope_key and self._scope_entities:
            if normalized_scope_key not in self._scope_entities:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_INVALID_SCOPE",
                    reason=f"Scope '{normalized_scope_key}' is not registered in the Ontology.",
                )
            if normalized_source_type == "FORM":
                allowed_entities_in_scope = self._scope_entities[normalized_scope_key]
                if ename not in allowed_entities_in_scope:
                    return OwnershipDecision(
                        verdict=OwnershipVerdict.DENY,
                        allowed=False,
                        code="OWNERSHIP_SCOPE_ENTITY_MISMATCH",
                        reason=(
                            f"Entity '{ename}' is not permitted in FORM scope '{normalized_scope_key}'."
                        ),
                    )

        entity_gov = self.resolve_entity(ename)

        # 3. Unknown entity check
        if entity_gov is None:
            norm_entity = _normalize_token(ename)
            if norm_entity in _BUSINESS_API_PROTECTED_PROPERTY_TOKENS:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_BUSINESS_API_DENY",
                    reason=(
                        f"Entity '{ename}' maps to protected BUSINESS_API domain "
                        f"'{_BUSINESS_API_PROTECTED_PROPERTY_TOKENS[norm_entity]}'."
                    ),
                    source_of_truth=SourceOfTruth.BUSINESS_API,
                    ingestion_mode=IngestionMode.DENY,
                )
            if effective_predicate and _normalize_token(effective_predicate) in _BUSINESS_API_PROTECTED_PROPERTY_TOKENS:
                target_field = _BUSINESS_API_PROTECTED_PROPERTY_TOKENS[_normalize_token(effective_predicate)]
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_BUSINESS_API_DENY",
                    reason=f"Predicate '{effective_predicate}' is owned by BUSINESS_API ({target_field}).",
                    source_of_truth=SourceOfTruth.BUSINESS_API,
                    ingestion_mode=IngestionMode.DENY,
                )
            return OwnershipDecision(
                verdict=OwnershipVerdict.REVIEW_REQUIRED,
                allowed=False,
                code="OWNERSHIP_UNKNOWN_ENTITY",
                reason=f"Entity '{ename}' is not registered in Layer 1/2/3 Ontology Registry.",
            )

        # 4. Priority 1: (entity_type, predicate) level ownership
        if effective_predicate:
            pred_gov: GovernanceMetadata | None = None
            if normalized_fact_type == FactType.RELATIONSHIP.value:
                pred_gov = self.resolve_relationship(ename, effective_predicate)
            else:
                pred_gov = self.resolve_property(ename, effective_predicate)
                if pred_gov is None:
                    pred_gov = self.resolve_relationship(ename, effective_predicate)

            if pred_gov is not None:
                if pred_gov.ingestion_mode == IngestionMode.DENY:
                    return OwnershipDecision(
                        verdict=OwnershipVerdict.DENY,
                        allowed=False,
                        code="OWNERSHIP_BUSINESS_API_DENY",
                        reason=(
                            f"Predicate '{ename}.{effective_predicate}' is owned by BUSINESS_API and "
                            "cannot be ingested into the Knowledge Graph."
                        ),
                        source_of_truth=pred_gov.source_of_truth,
                        ingestion_mode=pred_gov.ingestion_mode,
                    )
                if pred_gov.ingestion_mode == IngestionMode.REFERENCE_ONLY and not is_ref:
                    return OwnershipDecision(
                        verdict=OwnershipVerdict.DENY,
                        allowed=False,
                        code="OWNERSHIP_REFERENCE_ONLY",
                        reason=(
                            f"Predicate '{ename}.{effective_predicate}' is REFERENCE_ONLY; store only "
                            "BusinessRef IDs, not standalone facts."
                        ),
                        source_of_truth=pred_gov.source_of_truth,
                        ingestion_mode=pred_gov.ingestion_mode,
                    )
                if (
                    pred_gov.ingestion_mode == IngestionMode.STRUCTURED_ONLY
                    and normalized_source_type not in {None, "FORM"}
                ):
                    return OwnershipDecision(
                        verdict=OwnershipVerdict.DENY,
                        allowed=False,
                        code="OWNERSHIP_STRUCTURED_ONLY_DENY",
                        reason=(
                            f"Predicate '{ename}.{effective_predicate}' requires STRUCTURED_ONLY (FORM) ingestion."
                        ),
                        source_of_truth=pred_gov.source_of_truth,
                        ingestion_mode=pred_gov.ingestion_mode,
                    )
                return OwnershipDecision(
                    verdict=OwnershipVerdict.ALLOW,
                    allowed=True,
                    code="ALLOWED",
                    reason=f"Predicate '{ename}.{effective_predicate}' complies with Data Ownership Registry.",
                    source_of_truth=pred_gov.source_of_truth,
                    ingestion_mode=pred_gov.ingestion_mode,
                )

            # Predicate is NOT registered on `ename`.
            # Block disguised operational BUSINESS_API predicates even if attached to a Layer 2 entity like policy_rule!
            norm_pred = _normalize_token(effective_predicate)
            if norm_pred in _BUSINESS_API_PROTECTED_PROPERTY_TOKENS:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_SEMANTIC_ALIAS_DENY",
                    reason=(
                        f"Predicate '{effective_predicate}' on '{ename}' aliases protected BUSINESS_API field "
                        f"'{_BUSINESS_API_PROTECTED_PROPERTY_TOKENS[norm_pred]}'."
                    ),
                    source_of_truth=SourceOfTruth.BUSINESS_API,
                    ingestion_mode=IngestionMode.DENY,
                )

            # If entity itself is BUSINESS_API / DENY, reject immediately
            if (
                entity_gov.source_of_truth == SourceOfTruth.BUSINESS_API
                or entity_gov.ingestion_mode == IngestionMode.DENY
            ):
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_BUSINESS_API_DENY",
                    reason=(
                        f"Entity '{ename}' is owned by BUSINESS_API; unregistered predicate "
                        f"'{effective_predicate}' cannot be ingested."
                    ),
                    source_of_truth=entity_gov.source_of_truth,
                    ingestion_mode=IngestionMode.DENY,
                )

            # Unverified predicate on a Layer 2/3 entity -> do NOT fallback to entity ALLOW; require review
            return OwnershipDecision(
                verdict=OwnershipVerdict.REVIEW_REQUIRED,
                allowed=False,
                code="OWNERSHIP_UNVERIFIED_PREDICATE",
                reason=(
                    f"Predicate '{effective_predicate}' is not registered in schema for entity '{ename}'."
                ),
                source_of_truth=entity_gov.source_of_truth,
                ingestion_mode=entity_gov.ingestion_mode,
            )

        # 5. Missing predicate: only BUSINESS_REF on REFERENCE_ONLY/ALLOW entities is allowed without a property predicate;
        # otherwise deny if entity is DENY/REFERENCE_ONLY, or require review if predicate was omitted.
        if entity_gov.ingestion_mode == IngestionMode.DENY:
            return OwnershipDecision(
                verdict=OwnershipVerdict.DENY,
                allowed=False,
                code="OWNERSHIP_ENTITY_DENY",
                reason=f"Entity '{ename}' has ingestionMode=DENY.",
                source_of_truth=entity_gov.source_of_truth,
                ingestion_mode=entity_gov.ingestion_mode,
            )
        if entity_gov.ingestion_mode == IngestionMode.REFERENCE_ONLY:
            if is_ref:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.ALLOW,
                    allowed=True,
                    code="ALLOWED",
                    reason=f"BusinessRef link to '{ename}' is permitted.",
                    source_of_truth=entity_gov.source_of_truth,
                    ingestion_mode=entity_gov.ingestion_mode,
                )
            return OwnershipDecision(
                verdict=OwnershipVerdict.DENY,
                allowed=False,
                code="OWNERSHIP_REFERENCE_ONLY",
                reason=(
                    f"Entity '{ename}' is BUSINESS_API REFERENCE_ONLY; only "
                    "lightweight BusinessRef links are permitted."
                ),
                source_of_truth=entity_gov.source_of_truth,
                ingestion_mode=entity_gov.ingestion_mode,
            )

        return OwnershipDecision(
            verdict=OwnershipVerdict.REVIEW_REQUIRED,
            allowed=False,
            code="OWNERSHIP_MISSING_PREDICATE",
            reason=(
                f"Fact for entity '{ename}' is missing a verified schema predicate; "
                "cannot fallback to entity-level ownership without a valid predicate."
            ),
            source_of_truth=entity_gov.source_of_truth,
            ingestion_mode=entity_gov.ingestion_mode,
        )

    def check_layer3_proposal(
        self,
        proposal_type: str,
        payload: dict[str, Any],
        *,
        tenant_id: str | None,
        approved: bool = True,
    ) -> OwnershipDecision:
        """Validate a Layer 3 Tenant Extension schema proposal."""
        if not tenant_id or not tenant_id.strip():
            return OwnershipDecision(
                verdict=OwnershipVerdict.DENY,
                allowed=False,
                code="TENANT_REQUIRED",
                reason="Layer 3 schema proposals must be scoped to a valid tenant_id.",
            )
        if not approved:
            return OwnershipDecision(
                verdict=OwnershipVerdict.DENY,
                allowed=False,
                code="PROPOSAL_NOT_APPROVED",
                reason="Unapproved Layer 3 schema proposals cannot be used for ingestion or publish.",
            )

        target_name = str(payload.get("technicalName") or payload.get("technical_name") or "").strip()
        entity_name = str(payload.get("entityType") or payload.get("entity_type") or "").strip()
        norm_target = _normalize_token(target_name)
        norm_entity = _normalize_token(entity_name)

        owner_entity = self.resolve_entity(entity_name or target_name)
        if proposal_type in {"MODIFY_ENTITY_TYPE", "MODIFY_PROPERTY", "NEW_PROPERTY"}:
            if owner_entity is not None and owner_entity.extension_mode == ExtensionMode.LOCKED:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_LAYER1_LOCKED",
                    reason=(
                        f"Protected {owner_entity.source_of_truth.value} entity "
                        f"'{entity_name or target_name}' is LOCKED."
                    ),
                    source_of_truth=owner_entity.source_of_truth,
                )
            if owner_entity is not None and owner_entity.source_of_truth == SourceOfTruth.CORE_KNOWLEDGE:
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_LAYER2_PROTECTED",
                    reason=(
                        f"Core entity '{entity_name or target_name}' cannot be modified "
                        "directly by tenant Layer 3 proposals."
                    ),
                    source_of_truth=owner_entity.source_of_truth,
                )

        if proposal_type == "NEW_ENTITY_TYPE" and self.resolve_entity(target_name) is not None:
            existing = self.resolve_entity(target_name)
            return OwnershipDecision(
                verdict=OwnershipVerdict.DENY,
                allowed=False,
                code="OWNERSHIP_DUPLICATE_ENTITY",
                reason=f"Entity '{target_name}' already exists in {existing.layer if existing else 'core'}.",
                source_of_truth=existing.source_of_truth if existing else None,
            )

        # Anti-bypass: reject new properties/entities whose normalized name shadows BUSINESS_API fields
        if norm_target in _BUSINESS_API_PROTECTED_PROPERTY_TOKENS:
            return OwnershipDecision(
                verdict=OwnershipVerdict.DENY,
                allowed=False,
                code="OWNERSHIP_BYPASS_DENIED",
                reason=(
                    f"Proposed name '{target_name}' duplicates protected BUSINESS_API "
                    f"attribute '{_BUSINESS_API_PROTECTED_PROPERTY_TOKENS[norm_target]}'."
                ),
                source_of_truth=SourceOfTruth.BUSINESS_API,
                ingestion_mode=IngestionMode.DENY,
            )
        if norm_entity in _BUSINESS_API_PROTECTED_PROPERTY_TOKENS:
            return OwnershipDecision(
                verdict=OwnershipVerdict.DENY,
                allowed=False,
                code="OWNERSHIP_BYPASS_DENIED",
                reason=f"Proposed entity '{entity_name}' shadows BUSINESS_API domain.",
                source_of_truth=SourceOfTruth.BUSINESS_API,
                ingestion_mode=IngestionMode.DENY,
            )

        # Check semantic alias collision with Layer 2 entities
        for core_name, aliases in self._layer2_aliases.items():
            if norm_target and (
                norm_target == _normalize_token(core_name)
                or norm_target in {_normalize_token(a) for a in aliases}
            ):
                return OwnershipDecision(
                    verdict=OwnershipVerdict.DENY,
                    allowed=False,
                    code="OWNERSHIP_SEMANTIC_DUPLICATE",
                    reason=(
                        f"Proposed '{target_name}' semantically duplicates Layer 2 "
                        f"entity '{core_name}'."
                    ),
                    source_of_truth=SourceOfTruth.CORE_KNOWLEDGE,
                )

        return OwnershipDecision(
            verdict=OwnershipVerdict.ALLOW,
            allowed=True,
            code="ALLOWED",
            reason="Layer 3 proposal passed ownership and anti-bypass checks.",
            source_of_truth=SourceOfTruth.TENANT_EXTENSION,
            ingestion_mode=IngestionMode.ALLOW,
        )


__all__ = [
    "ExtensionMode",
    "FactType",
    "GovernanceMetadata",
    "IngestionMode",
    "OwnershipDecision",
    "OwnershipRegistry",
    "OwnershipVerdict",
    "SourceOfTruth",
    "VisibilityLevel",
]
