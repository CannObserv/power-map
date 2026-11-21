from enum import StrEnum


class ObservationPersonNameOp(StrEnum):
    OBSERVE = "observe"
    RETRACT = "retract"

    def __str__(self) -> str:
        return str(self.value)
