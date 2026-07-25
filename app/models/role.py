from sqlalchemy import Column, String

from app.db.session import Base


class Role(Base):
    __tablename__ = "role"
    __table_args__ = {"schema": "security"}

    role_code = Column(String(50), primary_key=True, nullable=False)
    role_name = Column(String(100), unique=True, nullable=False)
    description = Column(String(255))
