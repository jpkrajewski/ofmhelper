"""Things looked up by name: provider classes, platforms, catalog rows."""

from pydantic import BaseModel, ConfigDict


class Registry[T](BaseModel):
    """A read-only table, name -> entry, in the order it was built. What a
    function returns in place of a dict: `registry[name]` (KeyError when
    unknown), `registry.get(name)`, `registry.names()`, `registry.all()`."""

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
