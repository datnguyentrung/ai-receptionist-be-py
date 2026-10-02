"""Backfill GraphRAG chunks, facts, provenance, and embeddings for committed versions.

Run with:
    python -m app.commands.backfill_graphrag [--document-id UUID] [--limit 1000]
"""

import argparse
import asyncio

from app.core.ingestion_runtime import get_service_container, shutdown_service_container


async def backfill(*, document_id: str | None, limit: int) -> dict[str, int]:
    container = await get_service_container()
    workspaces = await container.repository.list_committed_workspaces(
        document_id=document_id,
        limit=limit,
    )
    result = {"scanned": 0, "backfilled": 0, "skipped": 0, "failed": 0}
    for workspace in workspaces:
        result["scanned"] += 1
        version_id = str(workspace.version.id)
        complete = await container.graph_store.is_graphrag_complete(
            version_id, len(workspace.chunks)
        )
        if complete:
            result["skipped"] += 1
            continue
        try:
            await container.graph_store.fill(workspace)
            result["backfilled"] += 1
        except Exception:  # noqa: BLE001 - continue so reruns can resume
            result["failed"] += 1
    return result


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    try:
        print(await backfill(document_id=args.document_id, limit=args.limit))
    finally:
        await shutdown_service_container()


if __name__ == "__main__":
    asyncio.run(_main())
