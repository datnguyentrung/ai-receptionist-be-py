"""Build the gate from settings; the NLI model is a process-wide singleton."""

from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import settings
from app.services.ingestion.schema.semantic_validation.decision_policy import DecisionPolicy
from app.services.ingestion.schema.semantic_validation.nli import (
    CachingNliVerifier,
    NliVerifier,
    TransformersNliVerifier,
)
from app.services.ingestion.schema.semantic_validation.semantic_gate import (
    LlmFallback,
    SemanticEvidenceGate,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _shared_nli() -> NliVerifier:
    return CachingNliVerifier(
        TransformersNliVerifier(
            settings.INGESTION_NLI_MODEL,
            device=settings.INGESTION_NLI_DEVICE,
            batch_size=settings.INGESTION_NLI_BATCH_SIZE,
        ),
        max_entries=settings.INGESTION_NLI_CACHE_SIZE,
    )


def build_semantic_gate(llm_fallback: LlmFallback | None = None) -> SemanticEvidenceGate | None:
    """None khi mode=off; shadow = chạy + log nhưng không chặn; enforce = chặn."""
    mode = settings.INGESTION_SEMANTIC_GATE_MODE.casefold()
    if mode not in {"shadow", "enforce"}:
        return None
    return SemanticEvidenceGate(
        nli=_shared_nli(),
        policy=DecisionPolicy(
            support_threshold=settings.INGESTION_NLI_SUPPORT_THRESHOLD,
            refute_threshold=settings.INGESTION_NLI_REFUTE_THRESHOLD,
            minimum_margin=settings.INGESTION_NLI_MINIMUM_MARGIN,
        ),
        llm_fallback=llm_fallback,
        enforce=mode == "enforce",
        block_uncertain=settings.INGESTION_SEMANTIC_GATE_BLOCK_UNCERTAIN,
    )
