from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CheckInResponse(BaseModel):
    matched: bool
    person_id: UUID | None = None
    confidence: float
    error: str | None = None

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
