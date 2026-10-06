from enum import StrEnum


class ListJurisdictionRelationshipsDirection(StrEnum):
    BOTH = "both"
    FROM = "from"
    TO = "to"

    def __str__(self) -> str:
        return str(self.value)
