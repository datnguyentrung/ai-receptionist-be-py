"""Build the model-facing extraction contract from a pinned ontology projection."""

import hashlib
import json
from typing import Any

from app.schemas.ingestion.ontology import OntologyProjection


def build_entity_extraction_contract(projection: OntologyProjection) -> dict[str, Any]:
    """Return a deterministic, data-driven contract for one ontology snapshot."""
    properties: dict[str, list[dict[str, Any]]] = {}
    for item in projection.properties:
        properties.setdefault(str(item["entityType"]), []).append(
            {
                "technicalName": item["technicalName"],
                "dataType": item["dataType"],
                "required": bool(item.get("required")),
            }
        )

    def _tech_name_key(item: dict[str, Any]) -> str:
        return str(item.get("technicalName", ""))

    entities: list[dict[str, Any]] = []
    for item in projection.entity_types:
        technical_name = str(item["technicalName"])
        entities.append(
            {
                "technicalName": technical_name,
                "identityFields": list(
                    (item.get("identityStrategy") or {}).get("required") or []
                ),
                "properties": sorted(
                    properties.get(technical_name, []),
                    key=_tech_name_key,
                ),
            }
        )
    relationships = [
        {
            "technicalName": item["technicalName"],
            "sourceEntityType": item["sourceEntityType"],
            "targetEntityType": item["targetEntityType"],
        }
        for item in projection.relationships
    ]
    contract = {
        "ontologyVersionId": projection.version_id,
        "ontologyDigest": projection.digest,
        "entities": sorted(entities, key=_tech_name_key),
        "relationships": sorted(relationships, key=_tech_name_key),
    }
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "contractHash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        **contract,
    }


__all__ = ["build_entity_extraction_contract"]
