from pydantic import BaseModel, ConfigDict


class Registry[T](BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    entries: dict[str, T]

    def __getitem__(self, name: str) -> T:
        return self.entries[name]

    def __contains__(self, name: object) -> bool:
        return name in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, name: str) -> T | None:
        return self.entries.get(name)

    def names(self) -> list[str]:
        return list(self.entries)

    def all(self) -> list[T]:
        return list(self.entries.values())
