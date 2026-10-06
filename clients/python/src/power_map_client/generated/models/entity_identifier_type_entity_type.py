from enum import StrEnum


class EntityIdentifierTypeEntityType(StrEnum):
    JURISDICTION = "jurisdiction"
    ORGANIZATION = "organization"
    PERSON = "person"
    ROLE_ASSIGNMENT = "role_assignment"

    def __str__(self) -> str:
        return str(self.value)
