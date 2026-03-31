from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.exceptions import ErrorCode, KaloscopeException
from app.core.media.handlers.base import MetaKeywords
from app.models.flow import FlowTrigger, GraphCategory
from app.models.media import MediaItem, MediaLib, MediaLibUpsert
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
        libs = await MediaLib.filter(id__in=ids)
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
            await watcher.add_observer(lib, initialize=True)

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
        # remove the observer
        watcher = cls.app_ctx().lib_watcher
        await watcher.remove_observer(lib.dir)


class MediaItemService(BaseService[MediaItem], model=MediaItem):
    """The service class for all media item related operations."""

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
        item, _ = await MediaItem.get_or_create(
            lib_id=lib_id,
            path=m.item_path,
            defaults={
                "dir": m.item_dir,
                "name": m.item_name,
                "parent_id": parent_id,
                # only top-level items are visible
                "visible": parent_id is None,
            },
        )
        return item
