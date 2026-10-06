from enum import StrEnum


class ObservationEventItemLinkedEntityTypeType0(StrEnum):
    ORGANIZATION = "organization"
    PERSON = "person"

    def __str__(self) -> str:
        return str(self.value)
