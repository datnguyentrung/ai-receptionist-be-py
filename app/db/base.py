from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2.x declarative models."""

    metadata = MetaData(schema="ontology")

