from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from app.memory_schemas import StrictModel


class SourceVersion(StrictModel):
    id: UUID
    version: str = Field(min_length=1, max_length=36)


class ChangeDetect(StrictModel):
    kind: Literal["document", "memory"]
    old: SourceVersion
    new: SourceVersion
    confirmed: StrictBool

    @model_validator(mode="after")
    def valid_pair(self):
        if not self.confirmed or self.old == self.new:
            raise ValueError("Explicit distinct version pair required")
        if self.kind == "memory":
            if self.old.id != self.new.id or not self.old.version.isascii() or not self.new.version.isascii():
                raise ValueError("Same memory required")
            if not self.old.version.isdigit() or not self.new.version.isdigit():
                raise ValueError("Invalid revision")
            if int(self.old.version) < 1 or str(int(self.old.version)) != self.old.version or str(int(self.new.version)) != self.new.version or int(self.new.version) != int(self.old.version) + 1:
                raise ValueError("Adjacent confirmed revisions required")
        else:
            UUID(self.old.version)
            UUID(self.new.version)
        return self
