from enum import StrEnum


class RoleTypeOccupancy(StrEnum):
    SHARED = "shared"
    SINGLE = "single"

    def __str__(self) -> str:
        return str(self.value)
