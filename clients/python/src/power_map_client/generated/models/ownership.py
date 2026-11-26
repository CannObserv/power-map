from enum import StrEnum


class Ownership(StrEnum):
    DATASET = "dataset"
    NONE = "none"
    OTHER = "other"
    SELF = "self"

    def __str__(self) -> str:
        return str(self.value)
