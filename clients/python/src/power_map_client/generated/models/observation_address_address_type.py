from enum import StrEnum


class ObservationAddressAddressType(StrEnum):
    MAILING = "mailing"
    OTHER = "other"
    PHYSICAL = "physical"

    def __str__(self) -> str:
        return str(self.value)
