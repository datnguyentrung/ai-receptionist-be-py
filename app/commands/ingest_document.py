"""Run one Markdown or UTF-8 text ingestion from the command line.

This is intentionally a thin E2E harness: schema and fact review remain human
gates and therefore return their pending payload instead of being auto-approved.
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService, Session
from google.adk.tools import ToolContext

from app.agent.skills.ingestion.batch_execution import BatchExecutionModule
from app.core.ingestion_runtime import get_service_container, shutdown_service_container
from app.services.ingestion.engine import operations


async def ingest(path: Path) -> dict:
    if path.suffix.casefold() not in {".md", ".txt"}:
        raise ValueError("Only Markdown and UTF-8 TXT are supported")
    document_key = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    container = await get_service_container()
    try:
        workspace = await operations.begin(
            container.repository, container.ontology_cache, path.name, path.read_bytes(),
            max_file_size=container.max_file_size, chunk_size_chars=container.chunk_size_chars,
            batch_size=container.batch_size, skill_digest=container.skill_digest,
            model_id=container.model_id, document_key=document_key,
            mime_type="text/markdown" if path.suffix.casefold() == ".md" else "text/plain",
        )
        if workspace.job.status.value == "COMMITTED":
            return operations.status_payload(workspace, idempotent=True)
        if workspace.job.summary.get("preview"):
            return await container.repository.get_preview(str(workspace.job.id))
        session_service = InMemorySessionService()
        session = Session(
            id=str(uuid.uuid4()), appName="ingestion_cli", userId="ingestion_cli", state={}
        )
        context = ToolContext(InvocationContext(
            session_service=session_service, invocation_id=str(uuid.uuid4()),
            session=session,
        ))
        executor = BatchExecutionModule()
        while True:
            pending = next((batch for batch in workspace.batches if batch.status != "STAGED"), None)
            if pending is None:
                break
            result = await executor.execute(str(workspace.job.id), pending.batch_index, context)
            if not result.get("success"):
                return result
            workspace = await operations.required_workspace(container.repository, str(workspace.job.id))
        workspace = await operations.finalize(container.repository, str(workspace.job.id))
        if workspace.job.status.value != "READY":
            return operations.status_payload(workspace)
        return await operations.build_preview(
            container.repository, container.graph_store, str(workspace.job.id),
            await container.ontology_lifecycle.ingestion_schema_changes(str(workspace.job.id)),
            await container.ontology_lifecycle.active_version_id(),
        )
    finally:
        await shutdown_service_container()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(ingest(args.document.resolve())), ensure_ascii=True, default=str))


if __name__ == "__main__":
    main()
