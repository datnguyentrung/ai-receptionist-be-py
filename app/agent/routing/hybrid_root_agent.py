"""Hybrid Root: Clef first, LLM fallback."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing

from google.adk.agents import BaseAgent, InvocationContext
from google.adk.events import Event, EventActions

from app.agent.routing.clef_router import route_with_clef
from app.utils.ingestion_logger import write_raw_trace

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.5

DIRECT_ROUTES = {
    "INGESTION_AGENT",
    "CALCULATION_AGENT",
    "GRAPH_QA_AGENT",
}

FALLBACK_AGENT = "llm_fallback_router"


def extract_message(ctx: InvocationContext) -> str:
    if not ctx.user_content:
        return ""

    return " ".join(
        part.text for part in (ctx.user_content.parts or []) if part.text
    ).strip()


def extract_history(ctx: InvocationContext) -> list[dict]:
    history = []

    # Chỉ lấy một phần hội thoại gần đây.
    for event in ctx.session.events[-12:]:
        if not event.content or not event.content.parts:
            continue

        if event.author == "user":
            role = "user"
        elif event.content.role == "model":
            role = "assistant"
        else:
            continue

        message = " ".join(
            part.text for part in event.content.parts if part.text
        ).strip()

        if message:
            history.append(
                {
                    "role": role,
                    "content": message[:1000],
                }
            )

    return history


class HybridRootAgent(BaseAgent):
    """Điều phối Clef trước, fallback về LLM Router."""

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:

        message = extract_message(ctx)
        history = extract_history(ctx)

        target_name = FALLBACK_AGENT
        source = "llm_fallback"
        confidence = None
        meaningful = None
        reason = "empty_message"

        write_raw_trace(
            "🧭 [HYBRID ROUTER EVALUATING]",
            {
                "userMessage": message,
                "historyCount": len(history),
            },
        )

        if message:
            try:
                # requests chạy trong thread,
                # không chặn event loop của ADK.
                result = await asyncio.to_thread(
                    route_with_clef,
                    message,
                    history,
                )

                confidence = result["confidence"]
                meaningful = result["meaningful"]
                chosen = result["target"]

                if (
                    confidence >= MIN_CONFIDENCE
                    and meaningful >= 0.5
                    and chosen in DIRECT_ROUTES
                ):
                    target_name = chosen
                    source = "clef"
                    reason = "accepted"
                else:
                    reason = (
                        f"low_confidence_or_unclear (confidence={confidence}, "
                        f"meaningful={meaningful}, target={chosen})"
                    )

            except Exception as exc:
                # Chỉ bắt lỗi ở tầng Clef.
                # Không bắt lỗi từ sub-agent nghiệp vụ.
                reason = f"clef_error: {exc!s}"
                logger.warning(
                    "Clef routing failed; using LLM fallback: %s",
                    exc,
                )
                write_raw_trace(
                    "⚠️ [HYBRID ROUTER FALLBACK ON CLEF ERROR]",
                    {"error": str(exc), "fallbackAgent": FALLBACK_AGENT},
                )

        routing_decision = {
            "source": source,
            "target": target_name,
            "confidence": confidence,
            "meaningful": meaningful,
            "reason": reason,
        }

        logger.info(
            "Routing source=%s target=%s confidence=%s reason=%s",
            source,
            target_name,
            confidence,
            reason,
        )

        write_raw_trace(
            "🚀 [HYBRID ROUTER DECISION]",
            routing_decision,
        )

        # Lưu metadata vào ADK Session thông qua Event.
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(
                state_delta={
                    "routing:last_source": source,
                    "routing:last_target": target_name,
                    "routing:last_confidence": confidence,
                    "routing:last_reason": reason,
                },
            ),
        )

        target_agent = self.find_agent(target_name)

        if target_agent is None:
            err_msg = f"Routing target not found: {target_name}"
            write_raw_trace("❌ [HYBRID ROUTER TARGET NOT FOUND]", {"error": err_msg})
            raise RuntimeError(err_msg)

        write_raw_trace(
            f"🔀 [HYBRID ROUTER DISPATCHING] {target_name}",
            {"target": target_name, "source": source},
        )

        # Chuyển tiếp toàn bộ event của sub-agent.
        async with aclosing(target_agent.run_async(ctx)) as events:
            async for event in events:
                yield event
