"""Unit tests for core transcoding."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.transcode import hls, tasks, transcoder
from app.core.transcode.options import TranscodeOptions


def test_hls_exports():
    package = importlib.import_module("app.core.transcode")
    hls = importlib.import_module("app.core.transcode.hls")
    names = (
        "TranscodeStats",
        "delete_output",
        "estimate_progress",
        "output_dir",
        "output_stats",
        "parse_profile",
        "read_m3u8",
        "scan_outputs",
    )

    assert all(getattr(package, name) is getattr(hls, name) for name in names)


def test_task_exports():
    package = importlib.import_module("app.core.transcode")
    tasks = importlib.import_module("app.core.transcode.tasks")
    names = (
        "delete_tasks",
        "finish_task",
        "list_tasks",
        "register_task",
        "stop_tasks",
    )

    assert all(getattr(package, name) is getattr(tasks, name) for name in names)


class _Lock:
    def __init__(self):
        self.locked = False

    def acquire(self):
        self.locked = True

    def release(self):
        self.locked = False


@pytest.mark.parametrize(
    ("encoded", "duration", "expected"),
    [(25, 100, 25), (100, 100, 99), (0, 100, None), (25, None, None)],
)
def test_progress(encoded, duration, expected):
    assert hls.estimate_progress(encoded, duration) == expected


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (
            "high_720p_nvenc",
            {"quality": "high", "resolution": "720p", "hwaccel": "nvenc"},
        ),
        (
            "medium_original_none",
            {"quality": "medium", "resolution": "original", "hwaccel": None},
        ),
        (
            "invalid",
            {"quality": None, "resolution": None, "hwaccel": None},
        ),
    ],
)
def test_parse_profile(profile, expected):
    assert hls.parse_profile(profile) == expected


def test_output_stats(tmp_path):
    playlist = tmp_path / "index.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXTINF:6.0,\nsegment_000000.ts\n"
        "#EXTINF:5.5,\nsegment_000001.ts\n#EXT-X-ENDLIST\n"
    )

    stats = hls.output_stats(tmp_path, duration=12)

    assert stats.finished is True
    assert stats.duration == 11.5
    assert stats.segments == 2
    assert stats.progress == 100


def test_software_cmd(monkeypatch, tmp_path):
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))

    cmd = asyncio.run(
        transcoder._build_hls_cmd("input.mkv", tmp_path, TranscodeOptions())
    )

    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-crf") + 1] == "23"
    assert cmd[cmd.index("-hls_time") + 1] == "6"
    assert "-vf" not in cmd


@pytest.mark.parametrize(
    "kwargs",
    [{"quality": "/tmp/outside"}, {"resolution": "../outside"}],
)
def test_options_reject_invalid(kwargs):
    with pytest.raises(ValueError):
        TranscodeOptions(**kwargs)


def test_rechecks_completion(monkeypatch, tmp_path):
    lock = object()
    complete = Mock(side_effect=[False, True])
    cleanup = Mock(side_effect=AssertionError("cleanup called"))
    release = Mock()
    options = TranscodeOptions()

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "_is_complete", complete)
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "_cleanup_stale_hls", cleanup)
    monkeypatch.setattr(transcoder, "_release_lock", release)

    result = asyncio.run(transcoder.ensure_transcode("input.mkv", "hash", options))

    assert result == ("hash", options.profile)
    assert complete.call_count == 2
    cleanup.assert_not_called()
    release.assert_called_once_with(lock)


def test_setup_failure_stops_process(monkeypatch, tmp_path):
    events = []

    async def communicate():
        events.append("wait")
        return b"", b""

    lock = object()
    proc = SimpleNamespace(
        pid=123,
        returncode=None,
        stderr=None,
        terminate=Mock(side_effect=lambda: events.append("terminate")),
        kill=Mock(),
        communicate=AsyncMock(side_effect=communicate),
    )
    release = Mock(side_effect=lambda _lock: events.append("release"))

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "_is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "_cleanup_stale_hls", Mock())
    monkeypatch.setattr(transcoder, "probe_framerate", AsyncMock(return_value=None))
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(
        transcoder.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    monkeypatch.setattr(transcoder, "probe_duration", AsyncMock(return_value=60.0))
    monkeypatch.setattr(
        transcoder, "register_task", AsyncMock(side_effect=RuntimeError("store failed"))
    )
    monkeypatch.setattr(transcoder, "_release_lock", release)

    with pytest.raises(RuntimeError, match="store failed"):
        asyncio.run(
            transcoder.ensure_transcode("input.mkv", "hash", TranscodeOptions())
        )

    assert events == ["terminate", "wait", "release"]
    proc.kill.assert_not_called()


def test_cleanup_kills_on_timeout():
    proc = SimpleNamespace(
        returncode=None,
        terminate=Mock(),
        kill=Mock(),
        communicate=AsyncMock(side_effect=[TimeoutError, (b"", b"")]),
    )

    asyncio.run(transcoder._terminate_ffmpeg(proc))

    proc.terminate.assert_called_once_with()
    proc.kill.assert_called_once_with()
    assert proc.communicate.await_count == 2


def test_shutdown_stops_monitors(monkeypatch):
    finish = AsyncMock()
    release = Mock()

    monkeypatch.setattr(transcoder, "finish_task", finish)
    monkeypatch.setattr(transcoder, "_release_lock", release)

    async def run():
        started = asyncio.Event()
        blocker = asyncio.Event()

        async def read():
            started.set()
            await blocker.wait()

        proc = SimpleNamespace(
            pid=123,
            returncode=None,
            stderr=SimpleNamespace(read=AsyncMock(side_effect=read)),
            terminate=Mock(),
            kill=Mock(),
            communicate=AsyncMock(return_value=(b"", b"")),
        )
        lock = object()
        task = transcoder._start_monitor(proc, lock, "task")
        assert task in transcoder._MONITOR_TASKS

        await started.wait()
        await transcoder.shutdown_monitors()
        return task, proc, lock

    task, proc, lock = asyncio.run(run())

    assert task.cancelled()
    assert not transcoder._MONITOR_TASKS
    proc.terminate.assert_called_once_with()
    finish.assert_awaited_once_with("task", 255)
    release.assert_called_once_with(lock)


def test_monitor_errors_logged(monkeypatch):
    error = Mock()

    async def fail(*_args):
        raise RuntimeError("monitor failed")

    monkeypatch.setattr(transcoder, "_monitor_ffmpeg", fail)
    monkeypatch.setattr(transcoder.logger, "error", error)

    async def run():
        task = transcoder._start_monitor(object(), object(), "task")
        assert task in transcoder._MONITOR_TASKS
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        return task

    task = asyncio.run(run())

    assert task not in transcoder._MONITOR_TASKS
    error.assert_called_once()


def test_timeout_keeps_lock(monkeypatch, tmp_path):
    lock = object()
    proc = SimpleNamespace(pid=123, returncode=None, stderr=None)
    release = Mock()

    monkeypatch.setattr(
        transcoder, "output_dir", lambda _hash, profile: tmp_path / profile
    )
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "probe_framerate", AsyncMock(return_value=None))
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(
        transcoder.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    monkeypatch.setattr(transcoder, "probe_duration", AsyncMock(return_value=60.0))
    monkeypatch.setattr(
        transcoder, "register_task", AsyncMock(return_value="hash:profile")
    )
    monkeypatch.setattr(transcoder, "_wait_segment", AsyncMock(return_value=False))
    monkeypatch.setattr(transcoder, "_release_lock", release)
    monkeypatch.setattr(transcoder, "_start_monitor", Mock())

    with pytest.raises(RuntimeError, match="not ready"):
        asyncio.run(
            transcoder.ensure_transcode(
                "input.mkv", "hash", TranscodeOptions(resolution="720p")
            )
        )

    release.assert_not_called()


def test_monitor_releases_lock(monkeypatch, tmp_path):
    proc = SimpleNamespace(stderr=None, returncode=0, wait=AsyncMock())
    lock = SimpleNamespace(lock_file=str(tmp_path / ".lock"))
    release = Mock()

    monkeypatch.setattr(
        transcoder, "finish_task", AsyncMock(side_effect=RuntimeError("store failed"))
    )
    monkeypatch.setattr(transcoder, "_release_lock", release)

    with pytest.raises(RuntimeError, match="store failed"):
        asyncio.run(transcoder._monitor_ffmpeg(proc, lock, "task"))

    release.assert_called_once_with(lock)


def test_list_releases_lock(monkeypatch):
    lock = _Lock()
    store = {"task": {"id": "task"}}

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, lock))
    monkeypatch.setattr(tasks, "scan_outputs", lambda: [])

    def snapshot(_task):
        assert lock.locked is False
        return {"id": "task", "started_at": "2026-01-01", "encoded_size": 0}

    monkeypatch.setattr(tasks, "_task_snapshot", snapshot)

    result = asyncio.run(tasks.list_tasks())

    assert [task["id"] for task in result] == ["task"]
