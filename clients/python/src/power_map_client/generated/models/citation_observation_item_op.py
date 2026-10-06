from enum import StrEnum


class CitationObservationItemOp(StrEnum):
    OBSERVE = "observe"
    RETRACT = "retract"

    def __str__(self) -> str:
        return str(self.value)
