import asyncio
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from itertools import groupby
from multiprocessing.synchronize import Event, Lock

from sanic import Sanic
from sanic.log import logger
from tortoise import timezone

from app.core.dl.adapter import load_config
from app.models.download import Downloader, DownloadState, DownloadTask


@dataclass(frozen=True)
class Unique:
    """The unique identifier of a download task."""

    id: str | None
    hash: str | None
    hash_v2: str | None

    def __eq__(self, other):
        if not isinstance(other, Unique):
            return False
        if not self.hash_v2 and not self.hash and not self.id:
            return not other.hash_v2 and not other.hash and not other.id
        return bool(
            (self.hash_v2 and self.hash_v2 == other.hash_v2)
            or (self.hash and self.hash == other.hash)
            or (self.id and self.id == other.id)
        )

    def __hash__(self):
        return hash(self.hash_v2 or self.hash or self.id or "")

    @staticmethod
    def from_task(task: DownloadTask) -> "Unique":
        """Create a unique identifier from a download task.

        Args:
            task: The download task.

        Returns:
            A unique identifier.
        """
        return Unique(
            id=task.unique_id,
            hash=task.info_hash,
            hash_v2=task.info_hash_v2,
        )


class DLSyncer:
    """The download task synchronizer."""

    _DL_SYNCER = "dl_syncer"
    _last_sync: datetime = datetime.now()

    def __init__(self, app: Sanic):
        """Initialize the download task synchronizer.

        Args:
            app: The Sanic application.
        """
        self._app = app
        self._task = None
        if self._syncer_lock.acquire(block=False):
            try:
                if not self._syncer_flag.is_set():
                    self._task = self._DL_SYNCER
                    self._syncer_flag.set()
            finally:
                self._syncer_lock.release()

    @cached_property
    def _syncer_lock(self) -> Lock:
        return self._app.shared_ctx.dl_syncer_lock

    @cached_property
    def _syncer_flag(self) -> Event:
        return self._app.shared_ctx.dl_syncer_flag

    @cached_property
    def _sync_fast(self) -> Event:
        return self._app.shared_ctx.dl_sync_fast

    def accelerate(self):
        """Accelerate the download synchronizer."""
        self._sync_fast.set()

    def decelerate(self):
        """Decelerate the download synchronizer."""
        self._sync_fast.clear()

    async def start(self):
        """Start the download synchronizer."""
        if self._task:
            self._app.add_task(self.sync(), name=self._task)

    async def shutdown(self):
        """Shutdown the download synchronizer."""
        if self._task:
            self._syncer_flag.clear()
            await self._app.cancel_task(self._task)

    async def sync(self):
        """Synchronize the download tasks."""
        while True:
            try:
                seconds = (datetime.now() - self._last_sync).total_seconds()
                if not self._sync_fast.is_set() and seconds < 30:
                    # do not synchronize too frequently
                    await asyncio.sleep(1)
                    continue

                all = await DownloadTask.filter(
                    state__in=[DownloadState.PAUSED, DownloadState.DOWNLOADING]
                )
                for downloader_id, tasks in groupby(all, key=lambda t: t.downloader_id):
                    downloader = await Downloader.get(id=downloader_id)
                    await sync_tasks(downloader, list(tasks))

                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Failed to synchronize the download tasks!", exc_info=True)
                await asyncio.sleep(1)
            finally:
                self._last_sync = datetime.now()


async def sync_tasks(downloader: Downloader, tasks: list[DownloadTask]):
    """Synchronize the download tasks with the specified downloader.

    Args:
        downloader: The downloader.
        tasks: The download tasks.
    """
    adapter = load_config(downloader.config)
    variables = {
        "ids": [t.unique_id for t in tasks if t.unique_id],
        "hashes": [t.info_hash for t in tasks if t.info_hash],
        "hashes_v2": [t.info_hash_v2 for t in tasks if t.info_hash_v2],
    }
    result = await adapter.call("list", variables)
    if not isinstance(result, list):
        return
    # construct a dict of unique id to item
    matched = {}
    for item in result:
        if isinstance(item, dict):
            unique = Unique(
                id=str(item.get("unique_id", "")),
                hash=str(item.get("info_hash", "")),
                hash_v2=str(item.get("info_hash_v2", "")),
            )
            matched[unique] = item
    # update the download tasks
    for task in tasks:
        item = matched.get(
            Unique(
                id=task.unique_id,
                hash=task.info_hash,
                hash_v2=task.info_hash_v2,
            )
        )
        # continue the loop if the task is not matched
        if not item:
            await DownloadTask.filter(id=task.id).update(
                up_speed=0 if task.up_speed is not None else None,
                dl_speed=0 if task.dl_speed is not None else None,
            )
            continue
        # update the state to `DOWNLOADING` if the download speed is greater than 0
        state = task.state
        up_speed = int(item.get("up_speed", task.up_speed) or 0)
        dl_speed = int(item.get("dl_speed", task.dl_speed) or 0)
        if state == DownloadState.PAUSED and task.dl_speed == 0 and dl_speed > 0:
            state = DownloadState.DOWNLOADING
        # update the state to `ERROR` if the error message is not empty
        error_msg = str(item.get("error_msg", ""))
        if error_msg:
            state = DownloadState.ERROR
        # update the state to `COMPLETED` if the percentage has reached 100
        percentage = float(item.get("percentage", 0.0))
        total_size = int(item.get("total_size", task.total_size) or 0)
        completed_size = int(item.get("completed_size", task.completed_size) or 0)
        completed_at = None
        if not percentage:
            percentage = completed_size / total_size * 100 if total_size else 0.0
        if percentage >= 100:
            dl_speed = 0
            completed_at = timezone.now()
            state = DownloadState.COMPLETED
        # update the download task
        await DownloadTask.filter(id=task.id).update(
            name=str(item.get("name", task.name) or ""),
            unique_id=str(item.get("unique_id", task.unique_id) or ""),
            raw_state=str(item.get("raw_state", task.raw_state) or ""),
            state=state,
            error_msg=error_msg,
            up_speed=up_speed,
            dl_speed=dl_speed,
            percentage=percentage,
            total_size=total_size,
            completed_size=completed_size,
            completed_at=completed_at,
        )
