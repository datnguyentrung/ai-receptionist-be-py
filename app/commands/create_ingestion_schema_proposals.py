"""Create validated schema proposals for a blocked ingestion batch from JSON."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.ingestion_runtime import get_service_container, shutdown_service_container
from app.services.ingestion.engine import operations


async def _run(ingestion_id: str, batch_index: int, proposal_file: Path) -> None:
    container = await get_service_container()
    try:
        workspace = await operations.required_workspace(container.repository, ingestion_id)
        definitions = json.loads(proposal_file.read_text(encoding="utf-8"))
        created = []
        for definition in definitions:
            proposal = await container.ontology_lifecycle.create_proposal(
                ingestion_id=ingestion_id,
                batch_index=batch_index,
                ontology_version_id=str(workspace.job.ontology_version_id),
                source_document_id=str(workspace.version.id),
                proposal_type=definition["proposalType"],
                technical_name=definition["technicalName"],
                reason=definition["reason"],
                payload=definition["payload"],
                evidence=definition.get("evidence", {}),
                affected_scope_keys=definition["affectedScopeKeys"],
            )
            created.append({"proposalId": str(proposal.id), **definition})
        await container.repository.block_batch_for_proposal(
            ingestion_id,
            batch_index,
            [{
                "code": "SCHEMA_PROPOSAL_PENDING",
                "message": f"{len(created)} schema proposals are waiting for review",
                "location": f"batch[{batch_index}]",
                "retryable": True,
            }],
        )
        print(json.dumps(created, ensure_ascii=True))
    finally:
        await shutdown_service_container()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ingestion_id")
    parser.add_argument("batch_index", type=int)
    parser.add_argument("proposal_file", type=Path)
    args = parser.parse_args()
    asyncio.run(_run(args.ingestion_id, args.batch_index, args.proposal_file))


if __name__ == "__main__":
    main()
