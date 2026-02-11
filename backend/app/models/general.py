from pydantic import BaseModel, Field, PositiveInt
from tortoise.fields import BooleanField, CharField, IntField, TextField

from app.models.base import Pageable, TortoiseModel


# -------------------- ORM Models -------------------- #
class GlobalVariable(TortoiseModel):
    key = CharField(max_length=64, unique=True)
    value = CharField(max_length=4096)
    value_length = IntField()
    encrypted = BooleanField()

    class Meta:
        table = "global_variable"
        ordering = ["-created_at"]


class GlobalCookie(TortoiseModel):
    name = TextField()
    value = TextField()
    domain = TextField()
    path = TextField()
    expires = IntField(null=True)

    class Meta:
        table = "global_cookie"
        unique_together = (("name", "domain", "path"),)


# -------------------- Pydantic Models -------------------- #
class VariableQuery(Pageable):
    key: str | None = None


class VariableUpsert(BaseModel):
    id: PositiveInt | None = None
    key: str | None = Field(min_length=1, max_length=64, default=None)
    value: str = Field(min_length=1, max_length=4096)
    encrypted: bool = False
