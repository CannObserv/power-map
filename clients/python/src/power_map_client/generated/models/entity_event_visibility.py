from enum import StrEnum


class EntityEventVisibility(StrEnum):
    HIDDEN = "hidden"
    LEGAL_ONLY = "legal_only"
    PUBLIC = "public"

    def __str__(self) -> str:
        return str(self.value)
