"""Step 3 Governance Pipeline: Safety, Scope, Ownership, Entity Resolution, Conflict, and Publication Gate."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.services.ontology.capability_registry import CapabilityRegistry
from app.services.ontology.ownership_registry import OwnershipRegistry, OwnershipVerdict


class GateSeverity(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    REJECT = "REJECT"
    QUARANTINE = "QUARANTINE"


class ValidatorOutcome(BaseModel):
    validator: str
    severity: GateSeverity
    code: str
    message: str


class CandidateFact(BaseModel):
    fact_id: str = ""
    entity_type: str
    property_name: str | None = None
    predicate: str | None = None
    fact_type: str = "PROPERTY"
    subject_key: str | None = None
    source_type: str | None = None
    form_type: str | None = None
    statement: str
    scope_key: str
    is_reference_only: bool = False
    business_ref_candidates: list[dict[str, Any]] = Field(default_factory=list)


class RejectedFact(BaseModel):
    fact: CandidateFact
    code: str
    reason: str


class ConflictRecord(BaseModel):
    candidate_statement: str
    existing_fact_id: str
    existing_statement: str
    reason: str


class GovernanceEvaluation(BaseModel):
    tenant_id: str
    overall_severity: GateSeverity
    validator_outcomes: list[ValidatorOutcome] = Field(default_factory=list)
    accepted_facts: list[CandidateFact] = Field(default_factory=list)
    rejected_facts: list[RejectedFact] = Field(default_factory=list)
    quarantined_segments: list[str] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    needs_entity_review: bool = False


_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"(?i)\b(?:ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|jailbreak)\b"),
    re.compile(r"(?i)\b(?:bypass\s+ownership|override\s+tenant|drop\s+table|match\s*\(n\)\s*detach\s+delete)\b"),
)
_PROFANITY_PATTERN = re.compile(
    r"(?i)\b(?:địt|đụ|lồn|cặc|buồi|chó\s*đẻ|fuck|shit|bitch|asshole)\b"
)
_PII_PATTERN = re.compile(
    r"(?i)(?:\b(?:cccd|cmnd|số\s*tài\s*khoản\s*cá\s*nhân|mật\s*khẩu)\b|\b0\d{11}\b|\b\d{12}\b)"
)
_DOMAIN_KEYWORDS = re.compile(
    r"(?i)\b(?:taekwondo|võ|đai|quyền|poomsae|sparring|đối\s*kháng|học\s*viên|hlv|huấn\s*luyện|lớp|học\s*thử|học\s*phí|trung\s*tâm|cơ\s*sở|thảm|nhập\s*học|tuyển\s*sinh|trại\s*hè|kỷ\s*luật|thể\s*lực|đăng\s*ký|ưu\s*đãi|bảo\s*lưu|nội\s*quy)\b"
)


class ContentSafetyGate:
    """Detects prompt injection, profanity, garbage text, and sensitive PII."""

    def evaluate(self, text: str) -> tuple[ValidatorOutcome, list[str]]:
        cleaned = (text or "").strip()
        if len(cleaned) < 5:
            return (
                ValidatorOutcome(
                    validator="ContentSafetyGate",
                    severity=GateSeverity.REJECT,
                    code="GARBAGE_OR_EMPTY",
                    message="Nội dung quá ngắn hoặc rỗng.",
                ),
                [],
            )

        # Garbage ratio check: e.g. "asdfghjkl!@#$%^&*123456" with no real multi-word prose
        alpha_tokens = re.findall(r"[A-Za-zÀ-ỹ]{2,}", cleaned)
        if len(alpha_tokens) < 2 or (len(cleaned) > 20 and len("".join(alpha_tokens)) / len(cleaned) < 0.35):
            return (
                ValidatorOutcome(
                    validator="ContentSafetyGate",
                    severity=GateSeverity.REJECT,
                    code="GARBAGE_TEXT",
                    message="Nội dung chứa chuỗi ký tự rác, không hợp lệ.",
                ),
                [],
            )

        quarantined: list[str] = []
        for pattern in _PROMPT_INJECTION_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                quarantined.append(match.group(0))
                return (
                    ValidatorOutcome(
                        validator="ContentSafetyGate",
                        severity=GateSeverity.QUARANTINE,
                        code="PROMPT_INJECTION_DETECTED",
                        message="Phát hiện dấu hiệu prompt injection; nội dung bị cách ly.",
                    ),
                    quarantined,
                )

        profanity_match = _PROFANITY_PATTERN.search(cleaned)
        if profanity_match:
            quarantined.append(profanity_match.group(0))
            return (
                ValidatorOutcome(
                    validator="ContentSafetyGate",
                    severity=GateSeverity.QUARANTINE,
                    code="PROFANITY_DETECTED",
                    message="Nội dung chứa ngôn từ không phù hợp / chửi bậy.",
                ),
                quarantined,
            )

        pii_match = _PII_PATTERN.search(cleaned)
        if pii_match:
            quarantined.append(pii_match.group(0))
            return (
                ValidatorOutcome(
                    validator="ContentSafetyGate",
                    severity=GateSeverity.QUARANTINE,
                    code="SENSITIVE_PII_DETECTED",
                    message="Phát hiện dữ liệu cá nhân nhạy cảm (CCCD/CMND/thông tin riêng tư).",
                ),
                quarantined,
            )

        return (
            ValidatorOutcome(
                validator="ContentSafetyGate",
                severity=GateSeverity.PASS,
                code="SAFE",
                message="Nội dung an toàn.",
            ),
            [],
        )


class ScopeRelevanceValidator:
    """Ensures content belongs to Taekwondo center advisory scopes."""

    def __init__(self, capability_registry: CapabilityRegistry | None = None) -> None:
        self._caps = capability_registry or CapabilityRegistry.default()

    def evaluate(self, text: str, scope_key: str) -> ValidatorOutcome:
        if not self._caps.is_scope_supported(scope_key):
            return ValidatorOutcome(
                validator="ScopeRelevanceValidator",
                severity=GateSeverity.REJECT,
                code="UNSUPPORTED_SCOPE",
                message=f"Scope '{scope_key}' không nằm trong Capability Registry.",
            )
        if not _DOMAIN_KEYWORDS.search(text or ""):
            return ValidatorOutcome(
                validator="ScopeRelevanceValidator",
                severity=GateSeverity.REJECT,
                code="OUT_OF_DOMAIN",
                message="Nội dung nằm ngoài lĩnh vực võ thuật Taekwondo / trung tâm.",
            )
        return ValidatorOutcome(
            validator="ScopeRelevanceValidator",
            severity=GateSeverity.PASS,
            code="IN_SCOPE",
            message="Nội dung phù hợp phạm vi sản phẩm.",
        )


class DataOwnershipValidator:
    """Evaluates candidate facts against OwnershipRegistry using schema & predicate metadata only."""

    def __init__(self, registry: OwnershipRegistry | None = None) -> None:
        self._registry = registry or OwnershipRegistry.from_seeds()

    def evaluate_facts(
        self, facts: list[CandidateFact]
    ) -> tuple[list[CandidateFact], list[RejectedFact], list[RejectedFact]]:
        accepted: list[CandidateFact] = []
        rejected: list[RejectedFact] = []
        review_required: list[RejectedFact] = []
        for fact in facts:
            decision = self._registry.check_fact_ingestion(
                fact.entity_type,
                fact.property_name,
                predicate=fact.predicate,
                fact_type=fact.fact_type,
                subject_key=fact.subject_key,
                source_type=fact.source_type,
                form_type=fact.form_type,
                scope_key=fact.scope_key,
                is_reference_only=fact.is_reference_only,
            )
            if decision.verdict == OwnershipVerdict.ALLOW:
                accepted.append(fact)
            elif decision.verdict == OwnershipVerdict.REVIEW_REQUIRED:
                review_required.append(
                    RejectedFact(fact=fact, code=decision.code, reason=decision.reason)
                )
            else:
                rejected.append(
                    RejectedFact(fact=fact, code=decision.code, reason=decision.reason)
                )
        return accepted, rejected, review_required

    def filter_facts(
        self, facts: list[CandidateFact]
    ) -> tuple[list[CandidateFact], list[RejectedFact]]:
        accepted, rejected, review_required = self.evaluate_facts(facts)
        return accepted, rejected + review_required


class ConflictValidator:
    """Detects contradictions against currently PUBLISHED facts of the same tenant."""

    @staticmethod
    def detect_conflicts(
        candidates: list[CandidateFact],
        published_facts: list[dict[str, Any]],
    ) -> list[ConflictRecord]:
        conflicts: list[ConflictRecord] = []
        for cand in candidates:
            cand_nums = set(re.findall(r"\b\d+\b", cand.statement))
            for pub in published_facts:
                if pub.get("entity_type") != cand.entity_type:
                    continue
                if pub.get("scope_key") != cand.scope_key:
                    continue
                pub_stmt = str(pub.get("statement", ""))
                pub_nums = set(re.findall(r"\b\d+\b", pub_stmt))
                # Same entity+scope with differing numeric policy values (e.g. 1 free trial vs 3 free trials)
                if cand_nums and pub_nums and cand_nums != pub_nums:
                    conflicts.append(
                        ConflictRecord(
                            candidate_statement=cand.statement,
                            existing_fact_id=str(pub.get("fact_id", "")),
                            existing_statement=pub_stmt,
                            reason="Mâu thuẫn số liệu chính sách với tri thức đang công bố cùng tenant.",
                        )
                    )
        return conflicts


class KnowledgeGovernancePipeline:
    """Unified ingestion + publication gate pipeline."""

    def __init__(
        self,
        ownership_registry: OwnershipRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self._safety = ContentSafetyGate()
        self._scope = ScopeRelevanceValidator(capability_registry)
        self._ownership = DataOwnershipValidator(ownership_registry)
        self._conflicts = ConflictValidator()

    def evaluate(
        self,
        *,
        tenant_id: str,
        scope_key: str,
        raw_text: str,
        candidate_facts: list[CandidateFact],
        published_facts: list[dict[str, Any]] | None = None,
        skip_scope_keyword_check: bool = False,
    ) -> GovernanceEvaluation:
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required for KnowledgeGovernancePipeline")

        outcomes: list[ValidatorOutcome] = []
        safety_outcome, quarantined = self._safety.evaluate(raw_text)
        outcomes.append(safety_outcome)
        if safety_outcome.severity == GateSeverity.REJECT:
            return GovernanceEvaluation(
                tenant_id=tenant_id,
                overall_severity=GateSeverity.REJECT,
                validator_outcomes=outcomes,
                quarantined_segments=quarantined,
            )

        # If safety flagged QUARANTINE on a multi-sentence document, isolate only unsafe
        # segments when safe, in-domain segments exist (except prompt injection which quarantines whole doc).
        working_facts = list(candidate_facts)
        if safety_outcome.severity == GateSeverity.QUARANTINE:
            if safety_outcome.code == "PROMPT_INJECTION_DETECTED" or len(candidate_facts) <= 1:
                return GovernanceEvaluation(
                    tenant_id=tenant_id,
                    overall_severity=GateSeverity.QUARANTINE,
                    validator_outcomes=outcomes,
                    quarantined_segments=quarantined,
                )
            safe_facts: list[CandidateFact] = []
            for fact in candidate_facts:
                seg_outcome, seg_q = self._safety.evaluate(fact.statement)
                if seg_outcome.severity in {GateSeverity.REJECT, GateSeverity.QUARANTINE}:
                    quarantined.extend(seg_q or [fact.statement])
                else:
                    safe_facts.append(fact)
            if not safe_facts:
                return GovernanceEvaluation(
                    tenant_id=tenant_id,
                    overall_severity=GateSeverity.QUARANTINE,
                    validator_outcomes=outcomes,
                    quarantined_segments=quarantined,
                )
            working_facts = safe_facts

        if not skip_scope_keyword_check:
            scope_outcome = self._scope.evaluate(raw_text, scope_key)
            outcomes.append(scope_outcome)
            if scope_outcome.severity == GateSeverity.REJECT:
                return GovernanceEvaluation(
                    tenant_id=tenant_id,
                    overall_severity=GateSeverity.REJECT,
                    validator_outcomes=outcomes,
                )

        accepted, rejected, review_required = self._ownership.evaluate_facts(working_facts)
        if rejected and not accepted and not review_required:
            outcomes.append(
                ValidatorOutcome(
                    validator="DataOwnershipValidator",
                    severity=GateSeverity.REJECT,
                    code="ALL_FACTS_OWNERSHIP_DENIED",
                    message="Tất cả fact đều vi phạm quy tắc sở hữu dữ liệu (BUSINESS_API).",
                )
            )
            return GovernanceEvaluation(
                tenant_id=tenant_id,
                overall_severity=GateSeverity.REJECT,
                validator_outcomes=outcomes,
                rejected_facts=rejected,
            )

        if review_required:
            outcomes.append(
                ValidatorOutcome(
                    validator="DataOwnershipValidator",
                    severity=GateSeverity.WARN,
                    code="OWNERSHIP_REVIEW_REQUIRED",
                    message=f"Có {len(review_required)} fact chưa xác định đủ predicate schema, cần kiểm duyệt.",
                )
            )

        needs_entity_review = bool(review_required) or any(
            len(f.business_ref_candidates) > 1 or f.business_ref_candidates == [{"ambiguous": True}]
            for f in accepted
        )
        rejected = rejected + review_required

        conflicts = self._conflicts.detect_conflicts(accepted, published_facts or [])
        if conflicts:
            outcomes.append(
                ValidatorOutcome(
                    validator="ConflictValidator",
                    severity=GateSeverity.WARN,
                    code="CONFLICT_PENDING_REVIEW",
                    message=f"Phát hiện {len(conflicts)} mâu thuẫn với tri thức đang công bố.",
                )
            )

        overall = (
            GateSeverity.WARN
            if (rejected or conflicts or needs_entity_review or quarantined)
            else GateSeverity.PASS
        )
        return GovernanceEvaluation(
            tenant_id=tenant_id,
            overall_severity=overall,
            validator_outcomes=outcomes,
            accepted_facts=accepted,
            rejected_facts=rejected,
            quarantined_segments=quarantined,
            conflicts=conflicts,
            needs_entity_review=needs_entity_review,
        )


__all__ = [
    "CandidateFact",
    "ConflictRecord",
    "ConflictValidator",
    "ContentSafetyGate",
    "DataOwnershipValidator",
    "GateSeverity",
    "GovernanceEvaluation",
    "KnowledgeGovernancePipeline",
    "RejectedFact",
    "ScopeRelevanceValidator",
    "ValidatorOutcome",
]
