from enum import StrEnum


class ChangeItemChangeKind(StrEnum):
    DELETED = "deleted"
    UPDATED = "updated"

    def __str__(self) -> str:
        return str(self.value)
