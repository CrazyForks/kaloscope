import asyncio
import queue
from datetime import UTC, datetime
from enum import Enum, auto
from functools import cached_property
from multiprocessing.managers import DictProxy, ListProxy
from multiprocessing.synchronize import Lock
from pathlib import Path
from queue import Queue

from sanic import Sanic
from sanic.log import Colors, logger
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

from app.core.media.handlers.base import MetaKeywords, get_handler
from app.core.media.shelver import is_nfo, update_metadata
from app.models.flow import GraphCategory
from app.models.media import MediaEvent, MediaItem, MediaLib
from app.models.user import HistoryType, UserHistory
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
        handler = get_handler(self._lib.lib_type)
        sys_event = handler.filter_event(event, base_path=self._lib.dir)
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


class LibAction(Enum):
    """The actions for the media library watcher."""

    SCAN = auto()
    REMOVE = auto()


class LibWatcher:
    """The media library watcher."""

    _LISTENER = "lib_listener"
    _observers: dict[str, tuple[BaseObserver, Queue]] = {}

    def __init__(self, app: Sanic):
        """Initialize the media library watcher.

        Args:
            app: The Sanic application instance.
        """
        self._app = app

    @cached_property
    def _watcher_lock(self) -> Lock:
        return self._app.shared_ctx.lib_watcher_lock

    @cached_property
    def _watcher_actions(self) -> DictProxy[str, LibAction]:
        return self._app.shared_ctx.lib_watcher_actions

    @cached_property
    def _scanning_paths(self) -> ListProxy[str]:
        return self._app.shared_ctx.lib_scanning_paths

    @cached_property
    def _observing_paths(self) -> ListProxy[str]:
        return self._app.shared_ctx.lib_observing_paths

    async def start(self):
        """Start the watcher."""
        libs = await MediaLib.all()
        for lib in libs:
            await self.add_observer(lib)
        self._app.add_task(self._listener(), name=self._LISTENER)

    async def shutdown(self):
        """Shutdown the watcher."""
        for path in list(self._observers.keys()):
            await self.remove_observer(path, force=True)
        await self._app.cancel_task(self._LISTENER)

    async def _listener(self):
        """Listen for the actions and perform the corresponding operations."""
        while True:
            try:
                for path in list(self._watcher_actions.keys()):
                    if path in self._observers:
                        action = self._watcher_actions.get(path)
                        if action == LibAction.SCAN:
                            await self.scan_directory(path)
                        elif action == LibAction.REMOVE:
                            await self.remove_observer(path)

                        self._watcher_actions.pop(path)
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Failed to process the watcher action!", exc_info=True)
                await asyncio.sleep(5)

    async def add_observer(self, lib: MediaLib):
        """Add a directory observer to monitor the specified path.

        Args:
            lib: The media library that will be monitored.
        """
        if self._watcher_lock.acquire(block=False):
            try:
                path = lib.dir
                if path not in self._observing_paths:
                    # create a new queue to store media events
                    events = await self._create_events(lib)
                    handler = EventHandler(lib, self._app.loop, events)
                    observer = Observer()
                    observer.schedule(handler, path, recursive=True)
                    observer.start()
                    self._observers[path] = (observer, events)
                    # create a task to consume events
                    self._app.add_task(self._event_consumer(events), name=encrypt(path))
                    # scan the directory for existing files
                    self._app.add_task(self._scan_directory(lib))
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
            self._watcher_actions[path] = LibAction.REMOVE
            return

        self._watcher_lock.acquire()
        try:
            if force or await MediaLib.filter(dir=path).count() == 0:
                observer, _ = self._observers.pop(path)
                observer.stop()
                observer.join()
                await self._app.cancel_task(encrypt(path))
                self._observing_paths.remove(path)
        finally:
            self._watcher_lock.release()

    async def _create_events(self, lib: MediaLib) -> Queue:
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

    async def scan_directory(self, target: MediaLib | str):
        """Scan the directory for existing files and create events.

        Args:
            target: The media library instance or the directory path to scan.
        """
        lib = None
        if isinstance(target, MediaLib):
            lib = target
            path = lib.dir
        else:
            path = target

        # check if the path is observed by the current worker
        if path not in self._observers:
            self._watcher_actions[path] = LibAction.SCAN
            return

        if lib is None:
            lib = await MediaLib.filter(dir=path).get()
        await self._scan_directory(lib)

    async def _scan_directory(self, lib: MediaLib):
        """Scan the directory for existing files and create events.

        Args:
            lib: The media library instance.
        """
        logger.info(f"Scanning directory: {Colors.GREEN}%s{Colors.END}", lib.dir)
        _, events = self._observers[lib.dir]

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

        # get all existing media items for this lib
        items = await MediaItem.filter(lib_id=lib.id).all()
        path_items = {item.path: item for item in items}
        nfo_mtimes = {item.nfo_path: item.nfo_mtime for item in items if item.nfo_path}
        # track which item ids still have their media file on disk
        existing_ids: list[int] = []

        nfo_events = []
        handler = get_handler(lib.lib_type)
        for depth in handler.hierarchies():
            pattern = "/".join("*" * depth) + ".*"
            for file in Path(lib.dir).glob(pattern):
                # skip directories
                if not file.is_file():
                    continue

                # skip files that are not accepted by the handler
                src_path = str(file)
                sys_event = handler.filter_event(
                    FileCreatedEvent(src_path), base_path=lib.dir
                )
                if sys_event is None:
                    continue

                if is_nfo(src_path):
                    # handle NFO file
                    if (nfo_mtime := nfo_mtimes.get(src_path)) is None:
                        # delay the NFO file event creation
                        nfo_events.append(sys_event)
                    else:
                        # check if mtime has been updated
                        mtime = datetime.fromtimestamp(file.stat().st_mtime, tz=UTC)
                        if mtime > nfo_mtime:
                            nfo_events.append(FileModifiedEvent(src_path))
                else:
                    # handle media file
                    if (media_item := path_items.get(src_path)) is None:
                        await _create_media_event(sys_event)
                    else:
                        existing_ids.append(media_item.id)
                        if media_item.nfo_path:
                            # check if the NFO file has been deleted
                            nfo_path = Path(media_item.nfo_path)
                            if not nfo_path.exists():
                                nfo_events.append(FileDeletedEvent(media_item.nfo_path))

        # create media events for NFO files
        for nfo_event in nfo_events:
            await _create_media_event(nfo_event)

        # create deletion events for missing media items
        for item in items:
            if item.id not in set(existing_ids) and not Path(item.path).exists():
                await _create_media_event(FileDeletedEvent(item.path))


async def consume_event(event: MediaEvent):
    """Consume the media event.

    Args:
        event: The media event.
    """
    result: list[MetaKeywords] | None = None
    async with in_transaction("default"):
        # delete the consumed event
        await event.delete()
        # handle the event based on its type
        if event.event_type == EVENT_TYPE_MODIFIED:
            await _handle_modified(event)
        elif event.event_type == EVENT_TYPE_DELETED:
            await _handle_deleted(event)
        elif event.event_type == EVENT_TYPE_MOVED:
            result = await _handle_moved(event)
        elif event.event_type == EVENT_TYPE_CREATED:
            result = await _handle_created(event)

    if result:
        for keywords in result:
            # parse the NFO file if it exists
            nfo_path = keywords.nfo_path
            if nfo_path is not None:
                await update_metadata(event.lib, nfo_path)

            # fire the flow triggers
            await FlowTriggerService.fire(
                GraphCategory.INGEST,
                event.lib_id,
                bootparams={
                    "item_path": keywords.item_path,
                    "item_name": keywords.item_name,
                    "nfo_path": str(nfo_path) if nfo_path else None,
                    "nfo_type": keywords.nfo_type,
                    "language": keywords.language,
                    "title": keywords.title,
                    "year": keywords.year,
                    "season": keywords.season,
                    "episode": keywords.episode,
                    "page_num": 1,
                    "page_size": 1,
                },
            )


async def _handle_modified(event: MediaEvent):
    """Handle the modification event.

    Args:
        event: The media event.
    """
    src_path = Path(event.src_path)
    if is_nfo(src_path):
        await update_metadata(event.lib, src_path)


async def _handle_deleted(event: MediaEvent):
    """Handle the deletion event.

    Args:
        event: The media event.
    """

    def _trash_nfo(path: str | None):
        if not path:
            return
        # move the NFO file to the trash if it exists
        nfo = Path(path)
        if nfo.exists() and nfo.is_file():
            send2trash(nfo)

    lib_id = event.lib_id
    src_path = event.src_path
    if event.is_directory:
        # delete all media items under the deleted directory
        ids: list = await MediaItem.filter(lib_id=lib_id, dir=src_path).values_list(
            "id", flat=True
        )
        if ids:
            await MediaItem.filter(lib_id=lib_id, id__in=ids).delete()
            # delete the related user histories
            await UserHistory.filter(
                rel_type=HistoryType.VIDEO, rel_id__in=ids
            ).delete()
    elif is_nfo(src_path):
        # update the media item to remove the NFO metadata
        await MediaItem.filter(lib_id=lib_id, nfo_path=src_path).update(
            nfo_path=None, nfo_mtime=None
        )
    else:
        # delete the media item
        item = await MediaItem.filter(lib_id=lib_id, path=src_path).get_or_none()
        if item is not None:
            await item.delete()
            _trash_nfo(item.nfo_path)
            # delete the related user histories
            await UserHistory.filter(
                rel_type=HistoryType.VIDEO, rel_id=item.id
            ).delete()
            # delete the parent item if it has no more children
            if (pid := item.parent_id) is not None:
                siblings = await MediaItem.filter(lib_id=lib_id, parent_id=pid).count()
                if siblings == 0:
                    parent_item = await MediaItem.filter(id=pid).get()
                    await parent_item.delete()
                    _trash_nfo(parent_item.nfo_path)


async def _handle_moved(event: MediaEvent) -> list[MetaKeywords] | None:
    """Handle the movement event.

    Args:
        event: The media event.
    """
    # delete the source media item if it exists
    await _handle_deleted(event)
    # create media items for the destination path
    return await _handle_created(event)


async def _handle_created(event: MediaEvent) -> list[MetaKeywords] | None:
    """Handle the creation event.

    Args:
        event: The media event.
    """
    # check if the destination path exists
    path = Path(event.dest_path or event.src_path)
    if not path.exists():
        return None

    # check if the destination path is an NFO file
    if is_nfo(path):
        await update_metadata(event.lib, path)
        return None

    # generate media items
    handler = get_handler(event.lib.lib_type)
    return await handler.gen_items(event.lib, path)
