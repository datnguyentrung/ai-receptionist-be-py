"""Publish one preview after the operator has explicitly approved its digest."""

import argparse
import asyncio
import json
import uuid
from types import SimpleNamespace

from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService, Session
from google.adk.tools import ToolContext

from app.agent.tools.ingestion_tools import commit_ingestion
from app.core.ingestion_runtime import shutdown_service_container


async def _run(ingestion_id: str, report_digest: str) -> None:
    context = ToolContext(
        InvocationContext(
            session_service=InMemorySessionService(),
            invocation_id=str(uuid.uuid4()),
            session=Session(id=str(uuid.uuid4()), appName="ingestion_cli", userId="human", state={}),
        ),
        function_call_id=str(uuid.uuid4()),
        tool_confirmation=SimpleNamespace(confirmed=True),
    )
    try:
        print(json.dumps(
            await commit_ingestion(ingestion_id, report_digest, context),
            ensure_ascii=True,
            default=str,
        ))
    finally:
        await shutdown_service_container()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ingestion_id")
    parser.add_argument("report_digest")
    args = parser.parse_args()
    asyncio.run(_run(args.ingestion_id, args.report_digest))


if __name__ == "__main__":
    main()
