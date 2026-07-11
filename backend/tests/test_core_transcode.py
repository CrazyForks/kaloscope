"""Unit tests for core transcoding."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.transcode import transcoder
from app.core.transcode.options import TranscodeOptions


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
    assert transcoder.estimate_progress(encoded, duration) == expected


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
    assert transcoder.parse_profile(profile) == expected


def test_output_stats(tmp_path):
    playlist = tmp_path / "index.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXTINF:6.0,\nsegment_000000.ts\n"
        "#EXTINF:5.5,\nsegment_000001.ts\n#EXT-X-ENDLIST\n"
    )

    stats = transcoder.output_stats(tmp_path, duration=12)

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

    def close_monitor(coro):
        coro.close()

    monkeypatch.setattr(transcoder.asyncio, "ensure_future", close_monitor)

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

    monkeypatch.setattr(transcoder, "_task_store", lambda: (store, lock))
    monkeypatch.setattr(transcoder, "scan_outputs", lambda: [])

    def snapshot(_task):
        assert lock.locked is False
        return {"id": "task", "started_at": "2026-01-01", "encoded_size": 0}

    monkeypatch.setattr(transcoder, "_task_snapshot", snapshot)

    tasks = asyncio.run(transcoder.list_tasks())

    assert [task["id"] for task in tasks] == ["task"]
