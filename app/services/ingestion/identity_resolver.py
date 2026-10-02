"""Derive canonical entity identity exclusively from compiled ontology metadata."""

from typing import Any

from app.schemas.ingestion_schema import OntologyProjection, PropertyFact


class IdentityResolutionError(ValueError):
    def __init__(
        self,
        *,
        class_name: str,
        missing_fields: list[str],
        unknown_class: bool = False,
    ) -> None:
        self.class_name = class_name
        self.missing_fields = missing_fields
        self.unknown_class = unknown_class
        message = (
            f"Unknown ontology entity type: {class_name}"
            if unknown_class
            else f"Missing ontology identity fields for {class_name}: {missing_fields}"
        )
        super().__init__(message)


class OntologyIdentityResolver:
    def __init__(self, projection: OntologyProjection) -> None:
        self._entity_contracts = {
            item["technicalName"]: item for item in projection.entity_types
        }

    def required_fields(self, class_name: str) -> list[str]:
        contract = self._entity_contracts.get(class_name)
        if contract is None:
            raise IdentityResolutionError(
                class_name=class_name,
                missing_fields=[],
                unknown_class=True,
            )
        strategy = contract.get("identityStrategy") or {}
        return list(strategy.get("required") or [])

    def resolve(
        self,
        *,
        class_name: str,
        properties: list[PropertyFact],
    ) -> dict[str, Any]:
        required = self.required_fields(class_name)
        property_values = {item.property_name: item.value for item in properties}
        missing = [
            field
            for field in required
            if field not in property_values or self._is_empty(property_values[field])
        ]
        if missing:
            raise IdentityResolutionError(
                class_name=class_name,
                missing_fields=missing,
            )
        return {
            field: self._normalize_identity_value(property_values[field])
            for field in required
        }

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    @staticmethod
    def _normalize_identity_value(value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


__all__ = ["IdentityResolutionError", "OntologyIdentityResolver"]
