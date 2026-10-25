from enum import StrEnum


class AssignmentListItemMembershipType(StrEnum):
    ACTING = "acting"
    EX_OFFICIO = "ex_officio"
    PRINCIPAL = "principal"
    SUBSTITUTE = "substitute"

    def __str__(self) -> str:
        return str(self.value)
