from enum import StrEnum


class ObservationEventItemOp(StrEnum):
    OBSERVE = "observe"
    RETRACT = "retract"

    def __str__(self) -> str:
        return str(self.value)
