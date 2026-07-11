import asyncio
import os
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from app.core.transcode.options import TranscodeOptions
from app.utils.disk import format_bytes

_TASKS: dict[str, dict[str, Any]] = {}
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


def _same_task(current: dict[str, Any], expected: dict[str, Any]) -> bool:
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
    tasks, lock = _task_store()
    lock.acquire()
    try:
        tasks[task_id] = {
            "id": task_id,
            "name": Path(media_path).name,
            "path": media_path,
            "hash": media_hash,
            "state": "running",
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
        task = dict(task)
    finally:
        lock.release()

    if returncode != 0:
        _remove_endlist(task.get("out_dir"))

    lock.acquire()
    try:
        current = tasks.get(task_id)
        if current is None:
            return
        current = dict(current)
        if not _same_task(current, task):
            return
        current["finished_at"] = datetime.now(UTC).isoformat()
        if returncode == 0:
            current["state"] = "finished"
        elif current["state"] == "stopping" or returncode == 255:
            current["state"] = "stopped"
        else:
            current["state"] = "error"
            current["error_msg"] = error_msg
        tasks[task_id] = current
    finally:
        lock.release()


async def list_tasks() -> list[dict[str, Any]]:
    """List runtime and finished filesystem transcode tasks.

    Returns:
        Runtime task snapshots followed by unregistered filesystem outputs.
    """
    store, lock = _task_store()
    lock.acquire()
    try:
        stored_tasks = [dict(task) for task in store.values()]
    finally:
        lock.release()
    tasks = [_task_snapshot(task) for task in stored_tasks]
    tasks.sort(key=lambda task: task["started_at"], reverse=True)

    task_ids = {task["id"] for task in tasks}
    scanned_tasks = [task for task in scan_outputs() if task["id"] not in task_ids]
    scanned_tasks.sort(key=lambda task: task["finished_at"], reverse=True)
    tasks.extend(scanned_tasks)

    for task in tasks:
        task["encoded_size_text"] = format_bytes(task["encoded_size"])
    return tasks


def _task_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    """Convert shared task metadata into an API-friendly dictionary.

    Args:
        task: The stored runtime task metadata.

    Returns:
        The task metadata enriched with output statistics.
    """
    snapshot = dict(task)
    stats = output_stats(snapshot.pop("out_dir"), snapshot.get("duration"))
    state = snapshot["state"]
    progress = (
        100
        if state == "finished"
        else estimate_progress(stats.duration, snapshot.get("duration"))
    )
    if state in ("error", "stopped") and progress is None:
        progress = 0

    snapshot.update(
        progress=progress,
        duration=snapshot.get("duration") or stats.duration or None,
        encoded_duration=stats.duration,
        encoded_segments=stats.segments,
        encoded_size=stats.size,
        finished_at=snapshot.get("finished_at") or stats.updated_at,
    )
    return snapshot


async def stop_tasks(ids: list[str]) -> list[str]:
    """Request termination for running shared transcode tasks.

    Args:
        ids: The task IDs to stop.

    Returns:
        The IDs of running tasks handled by the request.
    """
    claimed: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    tasks, lock = _task_store()
    lock.acquire()
    try:
        for task_id in ids:
            task = tasks.get(task_id)
            if task is None:
                continue
            task = dict(task)
            if task["state"] != "running":
                continue
            stopping = dict(task)
            stopping["state"] = "stopping"
            tasks[task_id] = stopping
            claimed.append((task_id, task, stopping))
    finally:
        lock.release()

    stopped: list[str] = []
    for task_id, original, stopping in claimed:
        try:
            if stopping.get("pid") is not None:
                sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(stopping["pid"], sigkill)
        except ProcessLookupError:
            terminal = dict(stopping)
            terminal["finished_at"] = datetime.now(UTC).isoformat()
            out_dir = terminal.get("out_dir")
            if out_dir and _is_complete(Path(out_dir) / "index.m3u8"):
                terminal["state"] = "finished"
            else:
                terminal["state"] = "stopped"
                _remove_endlist(out_dir)
            lock.acquire()
            try:
                current = tasks.get(task_id)
                if current is not None:
                    current = dict(current)
                    if current["state"] == "stopping" and _same_task(current, original):
                        tasks[task_id] = terminal
            finally:
                lock.release()
        except Exception:
            lock.acquire()
            try:
                current = tasks.get(task_id)
                if current is not None:
                    current = dict(current)
                    if current["state"] == "stopping" and _same_task(current, original):
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
    candidates: list[tuple[str, dict[str, Any] | None, str, str, Path | None]] = []
    tasks, lock = _task_store()
    lock.acquire()
    try:
        for task_id in ids:
            task = tasks.get(task_id)
            if task is not None:
                task = dict(task)
            if task is not None and task["state"] in ("running", "stopping"):
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
