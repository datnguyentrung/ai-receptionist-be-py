"""Apply explicitly human-approved schema proposals to an ingestion DRAFT."""

import argparse
import asyncio
import json
import uuid
from types import SimpleNamespace

from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService, Session
from google.adk.tools import ToolContext

from app.agent.tools.ingestion_tools import apply_schema_proposal, rebase_ingestion, review_schema_proposal
from app.core.ingestion_runtime import shutdown_service_container


def _confirmed_context() -> ToolContext:
    context = ToolContext(InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id=str(uuid.uuid4()),
        session=Session(id=str(uuid.uuid4()), appName="ingestion_cli", userId="human", state={}),
    ))
    context._tool_confirmation = SimpleNamespace(confirmed=True)  # ADK confirmation supplied by the caller.
    return context


async def _run(ingestion_id: str, proposal_ids: list[str]) -> None:
    context = _confirmed_context()
    try:
        reviews = [
            await review_schema_proposal(proposal_id, True, context)
            for proposal_id in proposal_ids
        ]
        applied = [await apply_schema_proposal(proposal_id, context) for proposal_id in proposal_ids]
        target_version_id = applied[-1]["ontologyVersionId"]
        rebase = await rebase_ingestion(ingestion_id, target_version_id, context)
        print(json.dumps({"reviews": reviews, "applied": applied, "rebase": rebase}, ensure_ascii=True))
    finally:
        await shutdown_service_container()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ingestion_id")
    parser.add_argument("proposal_id", nargs="+")
    args = parser.parse_args()
    asyncio.run(_run(args.ingestion_id, args.proposal_id))


if __name__ == "__main__":
    main()
