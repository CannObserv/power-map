from enum import StrEnum


class ObservationOrgNameNameType(StrEnum):
    DBA = "dba"
    FORMER = "former"
    LEGAL = "legal"

    def __str__(self) -> str:
        return str(self.value)
