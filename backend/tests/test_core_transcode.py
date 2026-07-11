"""Unit tests for core transcoding."""

import asyncio
import importlib
import threading
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


def test_task_types():
    package = importlib.import_module("app.core.transcode")

    assert {state.value for state in package.TaskState} == {
        "running",
        "stopping",
        "stopped",
        "finished",
        "error",
    }
    assert package.TaskState.RUNNING == "running"
    assert "out_dir" in package.RuntimeTask.__required_keys__
    assert "out_dir" not in package.TaskSnapshot.__required_keys__
    assert "encoded_size" in package.TaskSnapshot.__required_keys__
    assert "encoded_size_text" in package.TaskSnapshot.__optional_keys__


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


def test_scan_skips_excluded(monkeypatch, tmp_path):
    (tmp_path / "hash" / "profile").mkdir(parents=True)
    output_stats = Mock(side_effect=AssertionError("output scanned"))

    monkeypatch.setattr(hls, "output_stats", output_stats)

    result = hls.scan_outputs(tmp_path, exclude_ids={"hash:profile"})

    assert result == []
    output_stats.assert_not_called()


def test_probe_media(monkeypatch):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                b'{"streams":[{"avg_frame_rate":"30000/1001"}],'
                b'"format":{"duration":"60.5"}}',
                b"",
            )
        ),
    )
    create = AsyncMock(return_value=proc)

    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(transcoder.asyncio, "create_subprocess_exec", create)

    duration, framerate = asyncio.run(transcoder._probe_media("input.mkv"))

    assert duration == 60.5
    assert framerate == pytest.approx(30000 / 1001)
    create.assert_awaited_once()
    args = create.await_args.args
    assert "format=duration:stream=avg_frame_rate" in args
    assert "json" in args


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (1, b"", (None, None)),
        (
            0,
            b'{"streams":[{"avg_frame_rate":"bad"}],"format":{"duration":"60"}}',
            (60.0, None),
        ),
        (
            0,
            b'{"streams":[{"avg_frame_rate":"24/1"}],"format":{"duration":"bad"}}',
            (None, 24.0),
        ),
    ],
)
def test_probe_media_invalid(monkeypatch, returncode, stdout, expected):
    proc = SimpleNamespace(
        returncode=returncode,
        communicate=AsyncMock(return_value=(stdout, b"")),
    )

    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(
        transcoder.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    assert asyncio.run(transcoder._probe_media("input.mkv")) == expected


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
    monkeypatch.setattr(
        transcoder, "_probe_media", AsyncMock(return_value=(60.0, None))
    )
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(
        transcoder.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
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


def test_ensure_probes_once(monkeypatch, tmp_path):
    lock = object()
    proc = SimpleNamespace(pid=123, returncode=None, stderr=None)
    probe = AsyncMock(return_value=(60.0, 24.0))
    register = AsyncMock(return_value="hash:profile")
    options = TranscodeOptions()

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "_is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "_cleanup_stale_hls", Mock())
    monkeypatch.setattr(transcoder, "_probe_media", probe)
    monkeypatch.setattr(
        transcoder,
        "probe_framerate",
        AsyncMock(side_effect=AssertionError("separate probe called")),
    )
    monkeypatch.setattr(
        transcoder,
        "probe_duration",
        AsyncMock(side_effect=AssertionError("separate probe called")),
    )
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(
        transcoder.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    monkeypatch.setattr(transcoder, "register_task", register)
    monkeypatch.setattr(transcoder, "_start_monitor", Mock())
    monkeypatch.setattr(transcoder, "_wait_segment", AsyncMock(return_value=True))

    result = asyncio.run(transcoder.ensure_transcode("input.mkv", "hash", options))

    assert result == ("hash", options.profile)
    assert options.framerate == 24.0
    probe.assert_awaited_once_with("input.mkv")
    register.assert_awaited_once_with(
        "input.mkv", "hash", options, tmp_path, proc, 60.0
    )


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
    monkeypatch.setattr(
        transcoder, "_probe_media", AsyncMock(return_value=(60.0, None))
    )
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(
        transcoder.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
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
    monkeypatch.setattr(tasks, "scan_outputs", lambda *, exclude_ids=None: [])

    def snapshot(_task):
        assert lock.locked is False
        return {"id": "task", "started_at": "2026-01-01", "encoded_size": 0}

    monkeypatch.setattr(tasks, "_task_snapshot", snapshot)

    result = asyncio.run(tasks.list_tasks())

    assert [task["id"] for task in result] == ["task"]


def test_list_offloads_scan(monkeypatch):
    main_thread = threading.get_ident()
    scan_threads = []
    store = {"task": {"id": "task"}}

    def snapshot(_task):
        scan_threads.append(threading.get_ident())
        return {"id": "task", "started_at": "2026-01-01", "encoded_size": 0}

    def scan_outputs(*, exclude_ids=None):
        scan_threads.append(threading.get_ident())
        return []

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(tasks, "_task_snapshot", snapshot)
    monkeypatch.setattr(tasks, "scan_outputs", scan_outputs)

    result = asyncio.run(tasks.list_tasks())

    assert [task["id"] for task in result] == ["task"]
    assert scan_threads
    assert all(thread != main_thread for thread in scan_threads)


def test_list_excludes_registered(monkeypatch):
    store = {"task": {"id": "task"}}

    def snapshot(_task):
        return {"id": "task", "started_at": "2026-01-01", "encoded_size": 0}

    def scan_outputs(*, exclude_ids=None):
        assert exclude_ids == {"task"}
        return []

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(tasks, "_task_snapshot", snapshot)
    monkeypatch.setattr(tasks, "scan_outputs", scan_outputs)

    result = asyncio.run(tasks.list_tasks())

    assert [task["id"] for task in result] == ["task"]


def test_finish_releases_lock(monkeypatch, tmp_path):
    lock = _Lock()
    store = {
        "task": {
            "state": "running",
            "out_dir": str(tmp_path),
            "started_at": "2026-01-01",
            "pid": 123,
        }
    }

    def remove_endlist(_out_dir):
        assert lock.locked is False

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, lock))
    monkeypatch.setattr(tasks, "_remove_endlist", remove_endlist)

    asyncio.run(tasks.finish_task("task", 1, "failed"))

    assert store["task"]["state"] == "error"


def test_stop_releases_lock(monkeypatch, tmp_path):
    lock = _Lock()
    store = {
        "task": {
            "state": "running",
            "out_dir": str(tmp_path),
            "started_at": "2026-01-01",
            "pid": 123,
        }
    }

    def kill(_pid, _signal):
        assert lock.locked is False

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, lock))
    monkeypatch.setattr(tasks.os, "kill", kill)

    result = asyncio.run(tasks.stop_tasks(["task"]))

    assert result == ["task"]
    assert store["task"]["state"] == "stopping"


def test_delete_releases_lock(monkeypatch, tmp_path):
    lock = _Lock()
    out_dir = tmp_path / "hash" / "profile"
    store = {
        "hash:profile": {
            "state": "finished",
            "out_dir": str(out_dir),
            "started_at": "2026-01-01",
            "pid": 123,
        }
    }

    def delete_output(_hash, _profile, root=None):
        assert lock.locked is False
        assert root == tmp_path
        return True

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, lock))
    monkeypatch.setattr(tasks, "delete_output", delete_output)

    result = asyncio.run(tasks.delete_tasks(["hash:profile"]))

    assert result == ["hash:profile"]
    assert not store


def test_delete_keeps_replacement(monkeypatch, tmp_path):
    lock = _Lock()
    original = {
        "state": "finished",
        "out_dir": str(tmp_path / "hash" / "profile"),
        "started_at": "2026-01-01",
        "pid": 123,
    }
    replacement = {
        "state": "running",
        "out_dir": original["out_dir"],
        "started_at": "2026-01-02",
        "pid": 456,
    }
    store = {"hash:profile": original}

    def delete_output(_hash, _profile, root=None):
        assert lock.locked is False
        store["hash:profile"] = replacement
        return True

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, lock))
    monkeypatch.setattr(tasks, "delete_output", delete_output)

    asyncio.run(tasks.delete_tasks(["hash:profile"]))

    assert store["hash:profile"] is replacement
