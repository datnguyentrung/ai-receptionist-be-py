"""Seed the ACTIVE Layer 1 & Layer 2 Taekwondo ontology into PostgreSQL.

Run with:
    python -m app.commands.seed_taekwondo_ontology
"""

import asyncio
from typing import Any

from app.db.session import engine
from app.services.ontology.seeds import ONTOLOGY_VERSION_CODE, seed_ontology

VERSION_CODE = ONTOLOGY_VERSION_CODE


async def seed() -> dict[str, Any]:
    return await seed_ontology(version_code=VERSION_CODE)


async def _main() -> None:
    try:
        print(await seed())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
