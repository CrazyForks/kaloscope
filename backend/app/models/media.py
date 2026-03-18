from enum import StrEnum, auto
from typing import Self

from pydantic import BaseModel, Field, PositiveInt, model_validator
from tortoise.fields import (
    BooleanField,
    CharEnumField,
    CharField,
    DatetimeField,
    DecimalField,
    ForeignKeyField,
    ForeignKeyNullableRelation,
    ForeignKeyRelation,
    IntField,
    ReverseRelation,
)

from app.models.base import Pageable, TortoiseModel
from app.models.flow import GraphRef
from app.utils.disk import is_directory


# -------------------- Enumerations --------------------
class LibType(StrEnum):
    MOVIE = auto()
    TV_SHOW = auto()


class MediaType(StrEnum):
    VIDEO = auto()
    AUDIO = auto()
    IMAGE = auto()
    TEXT = auto()


class NFOType(StrEnum):
    MOVIE = "movie"
    TV_SHOW = "tvshow"
    EPISODE = "episode"


class Language(StrEnum):
    EN_US = "en-US"
    ZH_CN = "zh-CN"


# -------------------- ORM Models --------------------
class MediaLib(TortoiseModel):
    lib_type = CharEnumField(max_length=16, enum_type=LibType)
    name = CharField(max_length=64, unique=True)
    dir = CharField(max_length=4096, unique=True)
    language = CharEnumField(max_length=16, enum_type=Language, null=True)
    priority = IntField(unique=True)
    # relational fields
    items: ReverseRelation["MediaItem"]
    events: ReverseRelation["MediaEvent"]

    class Meta:
        table = "media_lib"
        ordering = ["priority"]

    class PydanticMeta:
        exclude = ("items", "events")


class MediaItem(TortoiseModel):
    lib_id: int
    lib: ForeignKeyRelation[MediaLib] = ForeignKeyField(
        "models.MediaLib", related_name="items", db_index=True
    )
    parent_id: int | None
    parent: ForeignKeyNullableRelation["MediaItem"] = ForeignKeyField(
        "models.MediaItem", related_name="children", db_index=True, null=True
    )
    dir = CharField(max_length=4096)
    path = CharField(max_length=4096)
    name = CharField(max_length=255)
    visible = BooleanField(default=False)
    nfo_path = CharField(max_length=4096, null=True)
    nfo_mtime = DatetimeField(null=True)
    title = CharField(max_length=255, null=True)
    year = IntField(null=True)
    aired = CharField(max_length=64, null=True)
    season = IntField(null=True)
    episode = IntField(null=True)
    poster = CharField(max_length=255, null=True)
    backdrop = CharField(max_length=255, null=True)
    rating = DecimalField(max_digits=4, decimal_places=2, null=True)
    # relational fields
    children: ReverseRelation["MediaItem"]

    class Meta:
        table = "media_item"
        ordering = ["-created_at"]
        unique_together = (("lib", "path"),)


class MediaEvent(TortoiseModel):
    lib_id: int
    lib: ForeignKeyRelation[MediaLib] = ForeignKeyField(
        "models.MediaLib", related_name="events", db_index=True
    )
    src_path = CharField(max_length=4096)
    dest_path = CharField(max_length=4096, null=True)
    event_type = CharField(max_length=16)
    is_directory = BooleanField(default=False)

    class Meta:
        table = "media_event"
        ordering = ["created_at"]


# -------------------- Pydantic Models --------------------
class MediaLibUpsert(BaseModel):
    id: PositiveInt | None = None
    lib_type: LibType | None = None
    name: str = Field(min_length=1, max_length=64)
    dir: str | None = Field(min_length=1, max_length=4096, default=None)
    language: str | None = None
    triggers: list[GraphRef] | None = None

    @model_validator(mode="after")
    def check_dir(self) -> Self:
        if not self.id and (not self.dir or not is_directory(self.dir)):
            raise ValueError(f"Invalid directory: {self.dir}")
        return self


class MediaQuery(Pageable):
    lib_id: PositiveInt
    keyword: str | None = None


class MediaStream(BaseModel):
    path: str
