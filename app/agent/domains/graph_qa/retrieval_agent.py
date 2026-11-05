"""Deterministic Graph QA retrieval adapter."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, InvocationContext
from google.adk.events import Event
from pydantic import PrivateAttr

from app.agent.tools.graphrag_tools import retrieve_graph_qa_evidence
from app.services.graphrag.retrieval import (
    INSUFFICIENT_EVIDENCE,
    EvidenceBundle,
    EvidenceStatus,
    GraphEvidenceRetriever,
    GraphQaRequest,
)
from app.utils.ingestion_logger import write_raw_trace

QUESTION_STATE_KEY = "temp:graph_qa_question"
EVIDENCE_STATE_KEY = "temp:graph_qa_evidence"


class GraphQaRetrievalAdapter(BaseAgent):
    """Run the one Graph QA retrieval invocation without an LLM tool loop."""

    _retriever: GraphEvidenceRetriever | None = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        retriever: GraphEvidenceRetriever | None = None,
        name: str = "graph_qa_retrieval",
        description: str = "Truy xuất bằng chứng GraphRAG một lần cho Graph QA.",
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description, **kwargs)
        self._retriever = retriever

    @property
    def has_model(self) -> bool:
        """Explicitly document that this adapter never owns a generative model."""
        return False

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        question = _extract_message(ctx)

        if not question:
            evidence = EvidenceBundle(
                status=EvidenceStatus.INSUFFICIENT,
                abstention_message=INSUFFICIENT_EVIDENCE,
            )
        else:
            request = GraphQaRequest(question=question)
            if self._retriever is not None:
                evidence = await self._retriever.retrieve(request)
            else:
                evidence = await retrieve_graph_qa_evidence(request)

        payload = evidence.model_dump(mode="json")
        ctx.session.state[QUESTION_STATE_KEY] = question
        ctx.session.state[EVIDENCE_STATE_KEY] = payload
        write_raw_trace(
            "🔎 [GRAPH QA RETRIEVAL]",
            {
                "status": evidence.status,
                "passageCount": len(evidence.passages),
                "factCount": len(evidence.facts),
            },
        )
        yield Event(author=self.name, invocation_id=ctx.invocation_id)


def _extract_message(ctx: InvocationContext) -> str:
    if not ctx.user_content:
        return ""
    return " ".join(
        part.text for part in (ctx.user_content.parts or []) if part.text
    ).strip()


__all__ = [
    "EVIDENCE_STATE_KEY",
    "QUESTION_STATE_KEY",
    "GraphQaRetrievalAdapter",
]
