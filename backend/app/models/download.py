from enum import StrEnum, auto
from typing import Self

from pydantic import BaseModel, Field, PositiveInt, field_serializer, model_validator
from sanic.request.form import File
from tortoise.fields import (
    SET_NULL,
    BigIntField,
    CharEnumField,
    CharField,
    DatetimeField,
    FloatField,
    ForeignKeyField,
    ForeignKeyNullableRelation,
    ForeignKeyRelation,
    IntField,
    JSONField,
    ReverseRelation,
    TextField,
)

from app.core.renderer import duration
from app.models.base import IDs, Pageable, RequestFilesMixin, TortoiseModel
from app.models.media import MediaLib
from app.utils.disk import format_bytes


# -------------------- Enumerations --------------------
class DownloadState(StrEnum):
    DOWNLOADING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    ERROR = auto()


class TransferMethod(StrEnum):
    HARDLINK = auto()
    SYMLINK = auto()
    MOVE = auto()
    COPY = auto()


# -------------------- ORM Models --------------------
class Downloader(TortoiseModel):
    preset = CharField(max_length=16, unique=True, null=True)
    config = TextField()
    name = CharField(max_length=64, unique=True)
    host = CharField(max_length=255, null=True)
    port = IntField(null=True)
    version = CharField(max_length=32, null=True)
    priority = IntField(unique=True)
    # relational fields
    tasks: ReverseRelation["DownloadTask"]

    class Meta:
        table = "downloader"
        ordering = ["priority"]

    class PydanticMeta:
        exclude = ("tasks",)


class DownloadDir(TortoiseModel):
    path = CharField(max_length=4096, unique=True)
    last_used = DatetimeField()

    class Meta:
        table = "download_dir"
        ordering = ["-last_used"]


class DownloadTask(TortoiseModel):
    # https://tortoise.github.io/models.html#the-db-backing-field
    downloader_id: int
    downloader: ForeignKeyRelation[Downloader] = ForeignKeyField(
        "models.Downloader", related_name="tasks", db_index=True
    )
    dir = CharField(max_length=4096)
    name = CharField(max_length=255)
    unique_id = CharField(max_length=255, db_index=True, null=True)
    info_hash = CharField(max_length=40, unique=True, null=True)
    info_hash_v2 = CharField(max_length=68, unique=True, null=True)
    magnet_link = TextField(null=True)
    state = CharEnumField(max_length=16, enum_type=DownloadState)
    raw_state = CharField(max_length=32, null=True)
    error_msg = TextField(null=True)
    up_speed = BigIntField(null=True)
    dl_speed = BigIntField(null=True)
    percentage = FloatField(null=True)
    total_size = BigIntField(null=True)
    completed_size = BigIntField(null=True)
    completed_at = DatetimeField(null=True)
    files = JSONField[list[str] | None](null=True)
    transfer_to_id: int | None
    transfer_to: ForeignKeyNullableRelation[MediaLib] = ForeignKeyField(
        "models.MediaLib",
        related_name="tasks",
        db_index=True,
        null=True,
        on_delete=SET_NULL,
    )
    transfer_method = CharEnumField(max_length=16, enum_type=TransferMethod, null=True)

    def ratio(self) -> str:
        """Calculate the ratio of completed size to total size."""
        size = ""
        if self.completed_size is None or self.total_size is None:
            return size
        size = f"{format_bytes(self.completed_size)} / {format_bytes(self.total_size)}"
        if self.percentage is None:
            return size
        percentage = f"{round(self.percentage)}%"
        return f"{size} ({percentage})"

    def estimate(self) -> str:
        """Estimate the download speed and time remaining."""
        speed = ""
        if self.dl_speed is None:
            return speed
        speed = format_bytes(self.dl_speed) + "/s"
        if not self.dl_speed or not self.completed_size or not self.total_size:
            return speed
        eta = duration(
            (self.total_size - self.completed_size) / self.dl_speed, unit="seconds"
        )
        return f"{speed} ({eta})"

    class Meta:
        table = "download_task"
        ordering = ["-created_at"]

    class PydanticMeta:
        exclude = ("downloader",)
        computed = ("ratio", "estimate")


# -------------------- Pydantic Models --------------------
class DownloaderUpsert(BaseModel):
    id: PositiveInt | None = None
    preset: str | None = Field(max_length=16, default=None)
    config: str


class DownloadQuery(Pageable):
    downloader_id: int | None = None
    name: str | None = None
    state: DownloadState | None = None
    states: list[DownloadState] | None = None


class DownloadAdd(BaseModel, RequestFilesMixin):
    downloader_id: PositiveInt
    dir: str
    link: str | None = Field(pattern=r"^[^\n]*$", default=None)
    torrent: File | None = None
    pause: bool = False

    @model_validator(mode="after")
    def check_link_or_torrent(self) -> Self:
        link = self.link.strip() if self.link else None
        if not link and not self.torrent:
            raise ValueError("Either `link` or `torrent` must be provided.")
        return self

    @field_serializer("torrent")
    def serialize_torrent(self, torrent: File | None) -> tuple | None:
        if torrent:
            # convert to HTTPX multipart file encoding
            # https://www.python-httpx.org/advanced/clients/#multipart-file-encoding
            return (torrent.name, torrent.body, torrent.type)
        return None


class DownloadDel(IDs):
    local: bool = False


class DownloadStats(BaseModel):
    downloading: int = 0
    completed: int = 0
    up_speed: int = 0
    dl_speed: int = 0

    @field_serializer("downloading", "completed")
    def serialize_count(self, count: int) -> str:
        return f" ({count})" if count else ""

    @field_serializer("up_speed", "dl_speed")
    def serialize_speed(self, speed: int) -> str:
        return format_bytes(speed)
