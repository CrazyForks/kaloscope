import asyncio
import os
import signal
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from sanic import Sanic
from sanic.exceptions import SanicException

from app.core.transcode.hls import (
    _is_complete,
    _remove_endlist,
    delete_output,
    estimate_progress,
    output_stats,
    scan_outputs,
)
from app.core.transcode.options import (
    HWAccelType,
    QualityLevel,
    ResolutionLimit,
    TranscodeOptions,
)
from app.utils.disk import format_bytes


class TaskState(StrEnum):
    """Lifecycle state of a transcode task."""

    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FINISHED = "finished"
    ERROR = "error"


class RuntimeTask(TypedDict):
    """Task metadata stored in the cross-worker runtime mapping."""

    id: str
    name: str
    path: str
    hash: str
    state: TaskState
    duration: float | None
    pid: int | None
    profile: str
    quality: QualityLevel
    resolution: ResolutionLimit
    hwaccel: HWAccelType | None
    out_dir: str
    started_at: str
    finished_at: str | None
    error_msg: str | None


class TaskSnapshot(TypedDict):
    """API-facing task metadata enriched with HLS output statistics."""

    id: str
    name: str
    path: str | None
    hash: str
    state: TaskState
    duration: float | None
    pid: int | None
    profile: str
    quality: QualityLevel | None
    resolution: ResolutionLimit | None
    hwaccel: HWAccelType | None
    started_at: str | None
    finished_at: str | None
    error_msg: str | None
    progress: int | None
    encoded_duration: float
    encoded_segments: int
    encoded_size: int
    encoded_size_text: NotRequired[str]


_TASKS: dict[str, RuntimeTask] = {}
_TASKS_LOCK = threading.RLock()


def _task_store():
    """Return the cross-worker task store and lock, with a test fallback.

    Returns:
        A tuple containing the task mapping and its synchronization lock.
    """
    try:
        shared = Sanic.get_app().shared_ctx
        return shared.transcode_tasks, shared.transcode_tasks_lock
    except (AttributeError, SanicException):
        return _TASKS, _TASKS_LOCK


def _same_task(current: RuntimeTask, expected: RuntimeTask) -> bool:
    """Check whether two task snapshots describe the same process run."""
    keys = ("started_at", "pid", "out_dir")
    return all(current.get(key) == expected.get(key) for key in keys)


async def register_task(
    media_path: str,
    media_hash: str,
    options: TranscodeOptions,
    out_dir: Path,
    proc: asyncio.subprocess.Process,
    duration: float | None,
) -> str:
    """Register a newly started ffmpeg process in the shared task store.

    Args:
        media_path: The source media file path.
        media_hash: The source media hash.
        options: The transcode parameters used by the process.
        out_dir: The HLS output directory.
        proc: The running ffmpeg subprocess.
        duration: The total source duration in seconds, if known.

    Returns:
        The registered task ID.
    """
    task_id = f"{media_hash}:{options.profile}"
    task: RuntimeTask = {
        "id": task_id,
        "name": Path(media_path).name,
        "path": media_path,
        "hash": media_hash,
        "state": TaskState.RUNNING,
        "duration": duration,
        "pid": proc.pid,
        "profile": options.profile,
        "quality": options.quality,
        "resolution": options.resolution,
        "hwaccel": options.hwaccel,
        "out_dir": str(out_dir),
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "error_msg": None,
    }
    tasks, lock = _task_store()
    lock.acquire()
    try:
        tasks[task_id] = task
    finally:
        lock.release()
    return task_id


async def finish_task(
    task_id: str, returncode: int | None, error_msg: str | None = None
) -> None:
    """Update a registered ffmpeg task to its terminal state.

    Args:
        task_id: The registered task ID.
        returncode: The ffmpeg process exit code.
        error_msg: The captured ffmpeg error message, if available.
    """
    tasks, lock = _task_store()
    lock.acquire()
    try:
        task = tasks.get(task_id)
        if task is None:
            return
        task = cast(RuntimeTask, dict(task))
    finally:
        lock.release()

    if returncode != 0:
        _remove_endlist(task.get("out_dir"))

    lock.acquire()
    try:
        current = tasks.get(task_id)
        if current is None:
            return
        current = cast(RuntimeTask, dict(current))
        if not _same_task(current, task):
            return
        current["finished_at"] = datetime.now(UTC).isoformat()
        if returncode == 0:
            current["state"] = TaskState.FINISHED
        elif current["state"] == TaskState.STOPPING or returncode == 255:
            current["state"] = TaskState.STOPPED
        else:
            current["state"] = TaskState.ERROR
            current["error_msg"] = error_msg
        tasks[task_id] = current
    finally:
        lock.release()


async def list_tasks() -> list[TaskSnapshot]:
    """List runtime and finished filesystem transcode tasks.

    Returns:
        Runtime task snapshots followed by unregistered filesystem outputs.
    """
    store, lock = _task_store()
    lock.acquire()
    try:
        stored_tasks = [cast(RuntimeTask, dict(task)) for task in store.values()]
    finally:
        lock.release()

    return await asyncio.to_thread(_build_task_list, stored_tasks)


def _build_task_list(stored_tasks: list[RuntimeTask]) -> list[TaskSnapshot]:
    """Build API task snapshots from shared records and filesystem outputs.

    Args:
        stored_tasks: Copies of the runtime task records from the shared store.

    Returns:
        Runtime task snapshots followed by unregistered filesystem outputs.
    """
    tasks = [_task_snapshot(task) for task in stored_tasks]
    tasks.sort(key=lambda task: task["started_at"] or "", reverse=True)

    task_ids = {task["id"] for task in tasks}
    scanned_tasks = scan_outputs(exclude_ids=task_ids)
    scanned_tasks.sort(key=lambda task: task["finished_at"] or "", reverse=True)
    tasks.extend(scanned_tasks)

    for task in tasks:
        task["encoded_size_text"] = format_bytes(task["encoded_size"])
    return tasks


def _task_snapshot(task: RuntimeTask) -> TaskSnapshot:
    """Convert shared task metadata into an API-friendly dictionary.

    Args:
        task: The stored runtime task metadata.

    Returns:
        The task metadata enriched with output statistics.
    """
    stats = output_stats(task["out_dir"], task["duration"])
    state = task["state"]
    progress = (
        100
        if state == TaskState.FINISHED
        else estimate_progress(stats.duration, task["duration"])
    )
    if state in (TaskState.ERROR, TaskState.STOPPED) and progress is None:
        progress = 0

    return {
        "id": task["id"],
        "name": task["name"],
        "path": task["path"],
        "hash": task["hash"],
        "state": state,
        "duration": task["duration"] or stats.duration or None,
        "pid": task["pid"],
        "profile": task["profile"],
        "quality": task["quality"],
        "resolution": task["resolution"],
        "hwaccel": task["hwaccel"],
        "started_at": task["started_at"],
        "finished_at": task["finished_at"] or stats.updated_at,
        "error_msg": task["error_msg"],
        "progress": progress,
        "encoded_duration": stats.duration,
        "encoded_segments": stats.segments,
        "encoded_size": stats.size,
    }


async def stop_tasks(ids: list[str]) -> list[str]:
    """Request termination for running shared transcode tasks.

    Args:
        ids: The task IDs to stop.

    Returns:
        The IDs of running tasks handled by the request.
    """
    claimed: list[tuple[str, RuntimeTask, RuntimeTask]] = []
    tasks, lock = _task_store()
    lock.acquire()
    try:
        for task_id in ids:
            task = tasks.get(task_id)
            if task is None:
                continue
            task = cast(RuntimeTask, dict(task))
            if task["state"] != TaskState.RUNNING:
                continue
            stopping = cast(RuntimeTask, dict(task))
            stopping["state"] = TaskState.STOPPING
            tasks[task_id] = stopping
            claimed.append((task_id, task, stopping))
    finally:
        lock.release()

    stopped: list[str] = []
    for task_id, original, stopping in claimed:
        try:
            pid = stopping["pid"]
            if pid is not None:
                sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(pid, sigkill)
        except ProcessLookupError:
            terminal = cast(RuntimeTask, dict(stopping))
            terminal["finished_at"] = datetime.now(UTC).isoformat()
            out_dir = terminal.get("out_dir")
            if out_dir and _is_complete(Path(out_dir) / "index.m3u8"):
                terminal["state"] = TaskState.FINISHED
            else:
                terminal["state"] = TaskState.STOPPED
                _remove_endlist(out_dir)
            lock.acquire()
            try:
                current = tasks.get(task_id)
                if current is not None:
                    current = cast(RuntimeTask, dict(current))
                    if current["state"] == TaskState.STOPPING and _same_task(
                        current, original
                    ):
                        tasks[task_id] = terminal
            finally:
                lock.release()
        except Exception:
            lock.acquire()
            try:
                current = tasks.get(task_id)
                if current is not None:
                    current = cast(RuntimeTask, dict(current))
                    if current["state"] == TaskState.STOPPING and _same_task(
                        current, original
                    ):
                        tasks[task_id] = original
            finally:
                lock.release()
            raise
        stopped.append(task_id)
    return stopped


async def delete_tasks(ids: list[str]) -> list[str]:
    """Delete non-running transcode outputs and remove runtime records.

    Args:
        ids: The task IDs to delete.

    Returns:
        The task IDs removed from the task store or output workspace.
    """
    candidates: list[tuple[str, RuntimeTask | None, str, str, Path | None]] = []
    tasks, lock = _task_store()
    lock.acquire()
    try:
        for task_id in ids:
            task = tasks.get(task_id)
            if task is not None:
                task = cast(RuntimeTask, dict(task))
            if task is not None and task["state"] in (
                TaskState.RUNNING,
                TaskState.STOPPING,
            ):
                continue
            media_hash, sep, profile = task_id.partition(":")
            if not media_hash or not sep or not profile:
                continue
            root = Path(task["out_dir"]).parents[1] if task is not None else None
            candidates.append((task_id, task, media_hash, profile, root))
    finally:
        lock.release()

    deleted: list[str] = []
    for task_id, task, media_hash, profile, root in candidates:
        deleted_output = delete_output(media_hash, profile, root=root)
        removed_record = False
        if task is not None:
            lock.acquire()
            try:
                current = tasks.get(task_id)
                if current is not None and dict(current) == task:
                    tasks.pop(task_id, None)
                    removed_record = True
            finally:
                lock.release()
        if deleted_output or removed_record:
            deleted.append(task_id)
    return deleted
