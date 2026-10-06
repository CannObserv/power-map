from enum import StrEnum


class ObservationPersonNamePartsPrimaryIdentifierType0(StrEnum):
    FAMILY = "family"
    GIVEN = "given"
    MONONYM = "mononym"
    PATRONYMIC = "patronymic"

    def __str__(self) -> str:
        return str(self.value)
