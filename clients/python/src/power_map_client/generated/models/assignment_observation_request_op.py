from enum import StrEnum


class AssignmentObservationRequestOp(StrEnum):
    OBSERVE = "observe"
    RETRACT = "retract"

    def __str__(self) -> str:
        return str(self.value)
