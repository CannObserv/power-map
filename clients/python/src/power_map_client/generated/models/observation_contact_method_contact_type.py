from enum import StrEnum


class ObservationContactMethodContactType(StrEnum):
    EMAIL = "email"
    PHONE = "phone"

    def __str__(self) -> str:
        return str(self.value)
