from enum import Enum


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    BANNED = "BANNED"
    PENDING = "PENDING"
    DEACTIVATED = "DEACTIVATED"
