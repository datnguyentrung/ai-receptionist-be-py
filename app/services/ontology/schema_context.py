"""Read-only schema context shared by ingestion and Graph QA."""

from typing import Any

from app.services.ingestion.schema.ontology import OntologyCache


class SchemaContextService:
    """Load compiled ontology context for an explicitly pinned version."""

    def __init__(self, cache: OntologyCache) -> None:
        self._cache = cache

    async def list_scopes(self, ontology_version_id: str) -> dict[str, Any]:
        scopes = await self._cache.list_scopes(ontology_version_id)
        return {
            "ontologyVersionId": ontology_version_id,
            "scopes": [item.model_dump(by_alias=True, mode="json") for item in scopes],
        }

    async def load_context(
        self, scope_keys: list[str], ontology_version_id: str
    ) -> dict[str, Any]:
        projection = await self._cache.get_many(scope_keys, ontology_version_id)
        return {
            "ontologyVersionId": projection.version_id,
            "scopeKeys": projection.scope_keys,
            "schemaDigest": projection.digest,
            "schema": projection.model_dump(by_alias=True, mode="json"),
        }

    async def active_context(self, scope_keys: list[str]) -> dict[str, Any]:
        active = await self._cache.active_version()
        return await self.load_context(scope_keys, active.version_id)


__all__ = ["SchemaContextService"]
