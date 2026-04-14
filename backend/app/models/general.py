from pydantic import BaseModel, Field, PositiveInt
from tortoise.fields import (
    BooleanField,
    CharEnumField,
    CharField,
    ForeignKeyField,
    ForeignKeyNullableRelation,
    IntField,
    TextField,
)

from app.models.base import Pageable, TortoiseModel
from app.models.user import User, UserRole


# -------------------- ORM Models --------------------
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


class Notification(TortoiseModel):
    user_id: int | None
    user: ForeignKeyNullableRelation[User] = ForeignKeyField(
        "models.User", related_name="notifications", db_index=True, null=True
    )
    role = CharEnumField(max_length=16, enum_type=UserRole, null=True)
    title = CharField(max_length=255)
    content = TextField()
    seen = BooleanField(default=False)

    class Meta:
        table = "notification"
        ordering = ["-created_at"]

    class PydanticMeta:
        exclude = ("user",)


# -------------------- Pydantic Models --------------------
class VariableQuery(Pageable):
    key: str | None = None


class VariableUpsert(BaseModel):
    id: PositiveInt | None = None
    key: str | None = Field(min_length=1, max_length=64, default=None)
    value: str = Field(min_length=1, max_length=4096)
    encrypted: bool = False


class NotificationQuery(Pageable):
    seen: bool | None = None
