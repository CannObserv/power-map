from enum import StrEnum


class DiscoveryItemEntityType(StrEnum):
    JURISDICTION = "jurisdiction"
    ORGANIZATION = "organization"
    PERSON = "person"
    ROLE = "role"
    ROLE_ASSIGNMENT = "role_assignment"
    ROLE_ASSIGNMENT_RELATIONSHIP = "role_assignment_relationship"

    def __str__(self) -> str:
        return str(self.value)
