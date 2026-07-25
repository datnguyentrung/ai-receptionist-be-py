from enum import Enum


class AttendanceStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"
    MAKEUP = "MAKEUP"


class EvaluationStatus(str, Enum):
    PENDING = "PENDING"
    GOOD = "GOOD"
    AVERAGE = "AVERAGE"
    WEAK = "WEAK"
