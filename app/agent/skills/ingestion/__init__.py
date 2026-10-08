from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.skills.ingestion.ingestion import ingestion_skill

__all__ = ["ingestion_skill"]


def __getattr__(name: str):
    if name == "ingestion_skill":
        from app.agent.skills.ingestion.ingestion import ingestion_skill

        return ingestion_skill
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
