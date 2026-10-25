from enum import StrEnum


class AssignmentDetailMembershipType(StrEnum):
    ACTING = "acting"
    EX_OFFICIO = "ex_officio"
    PRINCIPAL = "principal"
    SUBSTITUTE = "substitute"

    def __str__(self) -> str:
        return str(self.value)
