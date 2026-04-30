import asyncio
import hashlib
from pathlib import Path

import aiofiles
from sanic import Sanic
from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.exceptions import ErrorCode, KaloscopeException
from app.core.media.handlers.base import MetaKeywords
from app.models.flow import FlowTrigger, GraphCategory
from app.models.media import MediaItem, MediaLib, MediaLibUpsert, MediaMetadata, NFOType
from app.models.user import PermType, UserPermission
from app.services.base import BaseService
from app.services.flow import FlowTriggerService


class MediaLibService(BaseService[MediaLib], model=MediaLib):
    """The service class for all media library related operations."""

    @classmethod
    @atomic()
    async def update_priorities(cls, ids: list):
        """Update the media library priorities.

        Args:
            ids: The sorted media library IDs.
        """
        libs = await MediaLib.all()
        if set(ids) != set(lib.id for lib in libs):
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        # avoid duplicate priorities
        priorities = [lib.priority for lib in libs]
        start_priority = 1 if min(priorities) > len(ids) else max(priorities) + 1
        for lib in libs:
            lib.priority = start_priority + ids.index(lib.id)
        await MediaLib.bulk_update(libs, fields=["priority"])

    @classmethod
    @atomic()
    async def upsert(cls, obj: MediaLibUpsert) -> MediaLib:
        """Create or update a media library.

        Args:
            obj: The media library data.

        Raises:
            KaloscopeException: If the name or directory already exists.

        Returns:
            The media library instance.
        """

        # check if the name or directory already exists
        filter = ~Q(id=obj.id) if obj.id else Q()
        if await MediaLib.filter(filter & Q(name=obj.name)).count() > 0:
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)
        if obj.dir and await MediaLib.filter(filter & Q(dir=obj.dir)).count() > 0:
            raise KaloscopeException(ErrorCode.DUPLICATE_DIRECTORY)

        if obj.id:
            # update the media library
            await MediaLib.filter(id=obj.id).update(
                name=obj.name,
                language=obj.language or None,
            )
            lib = await MediaLib.get(id=obj.id)
        else:
            # create the media library
            priorities: list = await MediaLib.all().values_list("priority", flat=True)
            lib = await MediaLib.create(
                lib_type=obj.lib_type,
                name=obj.name,
                dir=obj.dir,
                language=obj.language or None,
                priority=(max(priorities) + 1 if priorities else 1),
            )
            # add the observer
            watcher = cls.app_ctx().lib_watcher
            await watcher.add_observer(lib)

        # bind the flow triggers to the media library
        await FlowTriggerService.bind_triggers(
            GraphCategory.INGEST, lib.id, obj.triggers
        )

        return lib

    @classmethod
    @atomic()
    async def delete(cls, id: int):
        """Delete a media library.

        Args:
            id: The media library ID.
        """
        lib = await MediaLib.get(id=id)
        await MediaLib.filter(id=id).delete()
        await FlowTrigger.filter(category=GraphCategory.INGEST, rel_id=id).delete()
        await UserPermission.filter(rel_type=PermType.MEDIA_LIB, rel_id=id).delete()
        # remove the observer
        watcher = cls.app_ctx().lib_watcher
        await watcher.remove_observer(lib.dir)


class MediaItemService(BaseService[MediaItem], model=MediaItem):
    """The service class for all media item related operations."""

    HASH_READ_SIZE = 16 * 1024 * 1024  # 16MB

    @classmethod
    async def create(
        cls, lib_id: int, parent_id: int | None, m: MetaKeywords
    ) -> MediaItem:
        """Get or create a media item.

        Args:
            lib_id: The media library ID.
            parent_id: The parent media item ID, if any.
            m: The metadata keywords for the media item.

        Returns:
            The media item instance.
        """
        item, created = await MediaItem.get_or_create(
            lib_id=lib_id,
            path=m.item_path,
            defaults={
                "dir": m.item_dir,
                "name": m.item_name,
                "parent_id": parent_id,
                "year": m.year,
                "season": m.season,
                "episode": m.episode,
                "visible": True,
            },
        )
        if created:
            asyncio.create_task(cls.update_file_info(item.id, m.item_path))

        return item

    @classmethod
    async def update_file_info(cls, item_id: int, item_path: str) -> None:
        """Calculate and persist the hash and size of a media file.

        Args:
            item_id: The media item ID.
            item_path: The file path of the media item.
        """
        path = Path(item_path)
        if not path.is_file():
            return
        size = path.stat().st_size
        md5 = hashlib.md5()
        async with aiofiles.open(path, "rb") as f:
            md5.update(await f.read(cls.HASH_READ_SIZE))
        await MediaItem.filter(id=item_id).update(hash=md5.hexdigest(), size=size)

    @classmethod
    async def refresh_episodes(cls, item: MediaItem, meta: MediaMetadata):
        """Refresh the metadata of the episodes under a season.

        Args:
            item: The season media item.
            meta: The season metadata object.
        """
        from app.core.media.shelver import gen_nfo, get_nfo_path

        metadata = meta.metadata
        series_id = metadata.get("id")
        season = metadata.get("season", item.season)
        title = metadata.get("title", item.title)
        year = metadata.get("year", item.year)

        # check if the series_id is the same as the current one
        same_series = series_id and str(series_id) == str(item.unique_id)

        # get the flow engine from the app context
        engine = Sanic.get_app().ctx.flow_engine

        # get the episodes under the season
        episodes = await MediaItem.filter(parent_id=item.id)
        for episode in episodes:
            nfo_path = episode.nfo_path

            # skip if the season is the same and the NFO file already exists
            if same_series and nfo_path and Path(nfo_path).exists():
                same_season = episode.season == season
                if same_season:
                    continue

            # execute the flow to get the metadata for the episode
            results = await engine.execute(
                graph_id=meta.graph_id,
                bootparams={
                    "$manual": True,
                    "series_id": series_id,
                    "item_path": episode.path,
                    "item_name": episode.name,
                    "nfo_type": NFOType.EPISODE,
                    "language": item.lib.language,
                    "title": title,
                    "year": year,
                    "season": season,
                    "episode": episode.episode,
                    "page_num": 1,
                    "page_size": 1,
                },
            )

            # generate the NFO file for the episode
            if isinstance(results, list) and len(results) > 0:
                result = results[0]
                if isinstance(result, dict):
                    nfo_path = nfo_path or get_nfo_path(episode.path)
                    await gen_nfo(NFOType.EPISODE, nfo_path, result, overwrite=True)
