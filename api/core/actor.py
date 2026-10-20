"""Shared attribution for configuration and audit records."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class Actor:
    type: Literal["user", "moderator", "system"]
    id: UUID | None

    def __post_init__(self) -> None:
        if self.type == "system" and self.id is None:
            return
        if self.type in ("user", "moderator") and isinstance(self.id, UUID):
            return
        raise ValueError("actor.invalid")


SYSTEM = Actor(type="system", id=None)
