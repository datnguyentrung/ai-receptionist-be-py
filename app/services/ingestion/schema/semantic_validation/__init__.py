"""Semantic Evidence Gate: kiểm chứng evidence có *support* claim (không chỉ tồn tại)."""

from app.services.ingestion.schema.semantic_validation.claim_builder import (
    LabelCatalog,
    build_claims,
    build_edge_claim,
    build_property_claim,
)
from app.services.ingestion.schema.semantic_validation.decision_policy import (
    DecisionPolicy,
    aggregate,
)
from app.services.ingestion.schema.semantic_validation.models import (
    Claim,
    Finding,
    NliScores,
    RuleOutcome,
    RuleResult,
    Verdict,
)
from app.services.ingestion.schema.semantic_validation.nli import (
    CachingNliVerifier,
    NliPair,
    NliUnavailableError,
    NliVerifier,
    TransformersNliVerifier,
)
from app.services.ingestion.schema.semantic_validation.semantic_gate import (
    FallbackRequest,
    FallbackVerdict,
    LlmFallback,
    SemanticEvidenceGate,
    SemanticReport,
)

__all__ = [
    "CachingNliVerifier",
    "Claim",
    "DecisionPolicy",
    "FallbackRequest",
    "FallbackVerdict",
    "Finding",
    "LabelCatalog",
    "LlmFallback",
    "NliPair",
    "NliScores",
    "NliUnavailableError",
    "NliVerifier",
    "RuleOutcome",
    "RuleResult",
    "SemanticEvidenceGate",
    "SemanticReport",
    "TransformersNliVerifier",
    "Verdict",
    "aggregate",
    "build_claims",
    "build_edge_claim",
    "build_property_claim",
]
