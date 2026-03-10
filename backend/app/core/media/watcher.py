import asyncio
import queue
from datetime import UTC, datetime
from functools import cached_property
from multiprocessing.managers import ListProxy
from multiprocessing.synchronize import Lock
from pathlib import Path
from queue import Queue

from sanic import Sanic
from sanic.log import logger
from send2trash import send2trash
from tortoise.transactions import in_transaction
from watchdog.events import (
    EVENT_TYPE_CREATED,
    EVENT_TYPE_DELETED,
    EVENT_TYPE_MODIFIED,
    EVENT_TYPE_MOVED,
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from app.core.media.filters.base import get_filter
from app.core.media.parsers.base import NFOParser, get_parser
from app.models.flow import GraphCategory
from app.models.media import MediaEvent, MediaItem, MediaLib
from app.services.flow import FlowTriggerService
from app.utils.crypto import encrypt


class EventHandler(FileSystemEventHandler):
    """File system event handler."""

    def __init__(self, lib: MediaLib, loop: asyncio.AbstractEventLoop, events: Queue):
        """Initialize the event handler.

        Args:
            lib: The media library instance.
            loop: The event loop for the application.
            events: The queue to store media events.
        """
        self._lib = lib
        self._loop = loop
        self._events = events

    async def _persist(self, event: FileSystemEvent):
        """Persist the event to the database.

        Args:
            event: The file system event to persist.
        """
        filter = get_filter(self._lib.lib_type)
        sys_event = filter.do_filter(event, base_path=self._lib.dir)
        if sys_event is not None:
            media_event = await MediaEvent.create(
                lib_id=self._lib.id,
                src_path=sys_event.src_path,
                dest_path=sys_event.dest_path,
                event_type=sys_event.event_type,
                is_directory=sys_event.is_directory,
            )
            media_event.lib = self._lib
            self._events.put(media_event)

    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent):
        """Called when a file or directory is modified.

        Args:
            event: Event representing file/directory modification.
        """
        self._loop.create_task(self._persist(event))

    def on_deleted(self, event: DirDeletedEvent | FileDeletedEvent):
        """Called when a file or directory is deleted.

        Args:
            event: Event representing file/directory deletion.
        """
        self._loop.create_task(self._persist(event))

    def on_created(self, event: DirCreatedEvent | FileCreatedEvent):
        """Called when a file or directory is created.

        Args:
            event: Event representing file/directory creation.
        """
        self._loop.create_task(self._persist(event))

    def on_moved(self, event: DirMovedEvent | FileMovedEvent):
        """Called when a file or a directory is moved or renamed.

        Args:
            event: Event representing file/directory movement.
        """
        self._loop.create_task(self._persist(event))


class LibWatcher:
    """The media library watcher."""

    _REMOVAL_LISTENER = "lib_removal_listener"
    _observers: dict[str, BaseObserver] = {}

    def __init__(self, app: Sanic):
        """Initialize the media library watcher.

        Args:
            app: The Sanic application.
        """
        self._app = app

    @cached_property
    def _watcher_lock(self) -> Lock:
        return self._app.shared_ctx.lib_watcher_lock

    @cached_property
    def _removing_paths(self) -> ListProxy[str]:
        return self._app.shared_ctx.lib_removing_paths

    @cached_property
    def _observing_paths(self) -> ListProxy[str]:
        return self._app.shared_ctx.lib_observing_paths

    async def start(self):
        """Start the watcher."""
        libs = await MediaLib.all()
        for lib in libs:
            await self.add_observer(lib, initialize=False)
        self._app.add_task(self._removal_listener(), name=self._REMOVAL_LISTENER)

    async def shutdown(self):
        """Shutdown the watcher."""
        for path in list(self._observers.keys()):
            await self.remove_observer(path, force=True)
        await self._app.cancel_task(self._REMOVAL_LISTENER)

    async def _create_queue(self, lib: MediaLib) -> Queue:
        """Create a new queue for the specified media library.

        Args:
            lib: The media library instance.

        Returns:
            A new queue instance.
        """
        events = Queue()
        # load existing events from the database for the library
        for event in await MediaEvent.filter(lib_id=lib.id):
            event.lib = lib
            events.put(event)
        return events

    async def _scan_directory(
        self, lib: MediaLib, events: Queue, *, initialize: bool = False
    ):
        """Scan the directory for existing files and create events.

        Args:
            lib: The media library instance.
            events: The queue to store media events.
            initialize: Whether this is the first time to scan the directory.
        """

        async def _create_media_event(sys_event: FileSystemEvent):
            """Create a media event from a system event.

            Args:
                sys_event: The file system event.
            """
            media_event = await MediaEvent.create(
                lib_id=lib.id,
                src_path=sys_event.src_path,
                dest_path=sys_event.dest_path,
                event_type=sys_event.event_type,
                is_directory=sys_event.is_directory,
            )
            media_event.lib = lib
            events.put(media_event)

        if not initialize:
            # get all existing media items for this lib
            items = await MediaItem.filter(lib_id=lib.id).all()
            path_items = {item.path: item for item in items}
            meta_mtimes = {
                item.meta_path: item.meta_mtime for item in items if item.meta_path
            }
            # track which item ids still have their media file on disk
            existing_ids: list[int] = []

        nfo_events = []
        filter = get_filter(lib.lib_type)
        for depth in filter.hierarchies():
            pattern = "/".join("*" * depth) + ".*"
            for file in Path(lib.dir).glob(pattern):
                if not file.is_file():
                    continue

                src_path = str(file.resolve())
                sys_event = filter.do_filter(
                    FileCreatedEvent(src_path), base_path=lib.dir
                )
                if sys_event is None:
                    continue

                if NFOParser.is_nfo(src_path):
                    # handle NFO file
                    if initialize or (meta_mtime := meta_mtimes.get(src_path)) is None:
                        # delay the NFO file event creation
                        nfo_events.append(sys_event)
                    else:
                        # check if mtime has been updated
                        mtime = datetime.fromtimestamp(file.stat().st_mtime, tz=UTC)
                        if mtime > meta_mtime:
                            nfo_events.append(FileModifiedEvent(src_path))
                else:
                    # handle media file
                    if initialize or (media_item := path_items.get(src_path)) is None:
                        await _create_media_event(sys_event)
                    else:
                        existing_ids.append(media_item.id)
                        if media_item.meta_path:
                            # check if the NFO file has been deleted
                            meta_path = Path(media_item.meta_path)
                            if not meta_path.exists():
                                nfo_events.append(
                                    FileDeletedEvent(media_item.meta_path)
                                )

        # create media events for NFO files
        for nfo_event in nfo_events:
            await _create_media_event(nfo_event)

        # create deletion events for missing media items
        if not initialize:
            for item in items:
                if item.id not in set(existing_ids):
                    await _create_media_event(FileDeletedEvent(item.path))

    async def add_observer(self, lib: MediaLib, *, initialize: bool = False):
        """Add a directory observer to monitor the specified path.

        Args:
            lib: The media library that will be monitored.
            initialize: Whether this is the first time to initialize the observer.
        """
        if self._watcher_lock.acquire(block=False):
            try:
                path = lib.dir
                if path not in self._observing_paths:
                    # create a new queue to store media events
                    events = await self._create_queue(lib)
                    handler = EventHandler(lib, self._app.loop, events)
                    observer = Observer()
                    observer.schedule(handler, path, recursive=True)
                    observer.start()
                    self._observers[path] = observer
                    # create a task to consume events
                    self._app.add_task(self._event_consumer(events), name=encrypt(path))
                    # scan the directory for existing files
                    self._app.add_task(
                        self._scan_directory(lib, events, initialize=initialize)
                    )
                    self._observing_paths.append(path)
            finally:
                self._watcher_lock.release()

    async def remove_observer(self, path: str, *, force: bool = False):
        """Remove the observer for the specified path.

        Args:
            path: The directory path to stop monitoring.
            force: Whether to forcefully remove the observer.
        """

        if path not in self._observers:
            self._removing_paths.append(path)
            return

        self._watcher_lock.acquire()
        try:
            if force or await MediaLib.filter(dir=path).count() == 0:
                observer = self._observers.pop(path)
                observer.stop()
                observer.join()
                await self._app.cancel_task(encrypt(path))
                self._observing_paths.remove(path)
        finally:
            self._watcher_lock.release()

    async def _removal_listener(self):
        """Listen for removal paths and remove the matching observers."""
        while True:
            try:
                for path in self._removing_paths[:]:
                    if path in self._observers:
                        await self.remove_observer(path)
                        self._removing_paths.remove(path)
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Failed to remove the matching observers!", exc_info=True)
                await asyncio.sleep(5)

    async def _event_consumer(self, events: Queue):
        """Consume events from the queue and process them.

        Args:
            events: The queue to store media events.
        """
        while True:
            try:
                if not events.empty():
                    event: MediaEvent = events.get_nowait()
                    await consume_event(event)
                await asyncio.sleep(1)
            except queue.Empty:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Failed to consume the media event!", exc_info=True)
                await asyncio.sleep(1)


async def consume_event(event: MediaEvent):
    """Consume the media event.

    Args:
        event: The media event.
    """
    item: MediaItem | None = None
    async with in_transaction("default"):
        # delete the consumed event
        await event.delete()
        # handle the event based on its type
        if event.event_type == EVENT_TYPE_MODIFIED:
            await _handle_modified(event)
        elif event.event_type == EVENT_TYPE_DELETED:
            await _handle_deleted(event)
        elif event.event_type == EVENT_TYPE_CREATED:
            item = await _handle_created(event)
        elif event.event_type == EVENT_TYPE_MOVED:
            item = await _handle_moved(event)

    # fire the flow triggers if the item is created or moved
    if item is not None:
        await FlowTriggerService.fire(
            GraphCategory.INGEST,
            event.lib_id,
            bootparams={
                "event_type": event.event_type,
                "lib": {
                    "type": item.lib.lib_type,
                    "name": item.lib.name,
                },
                "item": {
                    "path": item.path,
                    "name": item.name,
                },
                "keyword": item.name,
                "language": item.lib.language,
                "page_num": 1,
                "page_size": 1,
            },
        )


async def _parse_nfo(lib: MediaLib, path: Path):
    """Parse the NFO file and update the media item.

    Args:
        lib: The media library instance.
        path: The path to the NFO file.
    """
    parser = get_parser(lib.lib_type)
    meta = await parser.parse(path)
    await MediaItem.filter(
        lib_id=lib.id,
        dir=str(path.parent.resolve()),
        name=path.stem,
    ).update(
        meta_path=meta.path,
        meta_mtime=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        title=meta.title,
        year=meta.year,
        cover=meta.cover,
        backdrop=meta.backdrop,
        rating=meta.rating,
    )


async def _handle_modified(event: MediaEvent):
    """Handle the modification event.

    Args:
        event: The media event.
    """
    src_path = Path(event.src_path)
    if NFOParser.is_nfo(src_path):
        await _parse_nfo(event.lib, src_path)


async def _handle_deleted(event: MediaEvent):
    """Handle the deletion event.

    Args:
        event: The media event.
    """
    lib_id = event.lib_id
    src_path = event.src_path
    if event.is_directory:
        # delete the media items in the directory
        await MediaItem.filter(lib_id=lib_id, dir=src_path).delete()
    elif NFOParser.is_nfo(src_path):
        # update the media item meta to None
        await MediaItem.filter(lib_id=lib_id, meta_path=src_path).update(
            meta_path=None, meta_mtime=None
        )
    else:
        # delete the media item
        item = await MediaItem.filter(lib_id=lib_id, path=src_path).get_or_none()
        if item is not None:
            await item.delete()
            # delete the NFO file if it exists
            if item.meta_path:
                nfo_path = Path(item.meta_path)
                if nfo_path.exists() and nfo_path.is_file():
                    send2trash(nfo_path)


async def _handle_created(event: MediaEvent) -> MediaItem | None:
    """Handle the creation event.

    Args:
        event: The media event.
    """
    event.dest_path = event.src_path
    return await _handle_moved(event)


async def _handle_moved(event: MediaEvent) -> MediaItem | None:
    """Handle the movement event.

    Args:
        event: The media event.
    """
    # delete the source media item if it exists
    await _handle_deleted(event)

    # check if the destination path exists
    dest_path = Path(event.dest_path)
    if not dest_path.exists():
        return None

    # check if the destination path is an NFO file
    if NFOParser.is_nfo(dest_path):
        await _parse_nfo(event.lib, dest_path)
        return None

    # get or create the destination media item
    item, _ = await MediaItem.get_or_create(
        lib_id=event.lib_id,
        dir=str(dest_path.parent.resolve()),
        path=str(dest_path.resolve()),
        name=dest_path.stem,
    )
    item.lib = event.lib

    # parse the NFO file if it exists
    nfo_path = Path(item.dir) / f"{item.name}.nfo"
    if nfo_path.exists() and nfo_path.is_file():
        await _parse_nfo(event.lib, nfo_path)

    return item
