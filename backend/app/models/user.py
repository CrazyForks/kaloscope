from datetime import datetime
from enum import StrEnum, auto
from typing import Any

from pydantic import BaseModel, Field, PositiveInt
from sanic.request.form import File
from tortoise.fields import (
    CharEnumField,
    CharField,
    ForeignKeyField,
    ForeignKeyRelation,
    JSONField,
    ReverseRelation,
)

from app.models.base import Pageable, RequestFilesMixin, TortoiseModel
from app.models.flow import FlowGraph


# -------------------- Enumerations --------------------
class UserRole(StrEnum):
    USER = auto()
    ADMIN = auto()


# -------------------- ORM Models --------------------
class User(TortoiseModel):
    username = CharField(max_length=64, unique=True)
    password = CharField(max_length=64)
    avatar = CharField(max_length=255, null=True)
    role = CharEnumField(max_length=16, enum_type=UserRole)
    preferences = JSONField[dict[str, Any] | None](null=True)
    # relational fields
    favorites: ReverseRelation["UserFavorite"]
    histories: ReverseRelation["UserHistory"]

    class Meta:
        table = "user"
        ordering = ["role", "-created_at"]

    class PydanticMeta:
        exclude = ("password", "favorites", "histories")


class UserSession(TortoiseModel):
    session_id = CharField(max_length=32)
    user_info = JSONField["UserInfo"]()

    class Meta:
        table = "user_session"


class UserFavorite(TortoiseModel):
    user_id: int
    user: ForeignKeyRelation[User] = ForeignKeyField(
        "models.User", related_name="favorites", db_index=True
    )
    indexer_id: int
    indexer: ForeignKeyRelation[FlowGraph] = ForeignKeyField(
        "models.FlowGraph", related_name="favorites", db_index=True
    )
    rsrc_id = CharField(max_length=255)
    rsrc = JSONField[dict[str, Any]]()
    url = CharField(max_length=255, null=True)

    class Meta:
        table = "user_favorite"
        ordering = ["-created_at"]

    class PydanticMeta:
        exclude = ("user", "indexer")


class UserHistory(TortoiseModel):
    user_id: int
    user: ForeignKeyRelation[User] = ForeignKeyField(
        "models.User", related_name="histories", db_index=True
    )

    class Meta:
        table = "user_history"
        ordering = ["-created_at"]

    class PydanticMeta:
        exclude = ("user",)


# -------------------- Pydantic Models --------------------
class UserInfo(BaseModel):
    id: PositiveInt
    login_id: str
    username: str
    avatar: str | None
    role: UserRole
    preferences: dict
    user_agent: str | None = None
    client_ip: str
    login_at: datetime
    expire_at: datetime
    last_activity: datetime


class UserLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class UserQuery(Pageable):
    username: str | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=6, max_length=64)
    confirm_pwd: str = Field(min_length=6, max_length=64)


class UserPwd(BaseModel):
    cur_pwd: str = Field(min_length=6, max_length=64)
    new_pwd: str = Field(min_length=6, max_length=64)
    confirm_pwd: str = Field(min_length=6, max_length=64)


class UserAvatar(BaseModel, RequestFilesMixin):
    avatar: File | None = None


class FavoriteQuery(Pageable):
    indexer_id: PositiveInt | None = None
    rsrc_ids: list[str] | None = Field(min_length=1, max_length=999, default=None)
