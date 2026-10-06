from enum import StrEnum


class DiscoverSubscriptionsRootType(StrEnum):
    JURISDICTION = "jurisdiction"
    ORGANIZATION = "organization"

    def __str__(self) -> str:
        return str(self.value)
