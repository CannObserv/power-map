from enum import StrEnum


class EntityEventTypeAppliesTo(StrEnum):
    BOTH = "both"
    ORGANIZATION = "organization"
    PERSON = "person"

    def __str__(self) -> str:
        return str(self.value)
