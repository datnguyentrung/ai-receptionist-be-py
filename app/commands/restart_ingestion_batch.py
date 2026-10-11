"""Operator command to re-extract an ingestion job from a selected batch."""

import argparse
import asyncio

from app.core.ingestion_runtime import get_service_container, shutdown_service_container


async def _run(ingestion_id: str, batch_index: int, reason: str) -> None:
    container = await get_service_container()
    try:
        workspace = await container.repository.restart_from_batch(
            ingestion_id, batch_index, reason
        )
        print(
            {
                "ingestionId": str(workspace.job.id),
                "status": workspace.job.status.value,
                "nextBatch": next(
                    (
                        batch.batch_index
                        for batch in workspace.batches
                        if batch.status != "STAGED"
                    ),
                    None,
                ),
            }
        )
    finally:
        await shutdown_service_container()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ingestion_id")
    parser.add_argument("batch_index", type=int)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.ingestion_id, args.batch_index, args.reason))


if __name__ == "__main__":
    main()
