"""Unit tests for core transcoding."""

import asyncio
import importlib
import threading
from dataclasses import fields, replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from filelock import FileLock

import app.core.transcode.hwaccels.base as base_module
import app.core.transcode.hwaccels.qsv as qsv_module
import app.core.transcode.hwaccels.vaapi as vaapi_module
from app.core.exceptions import KaloscopeException
from app.core.transcode import capabilities as capability_module
from app.core.transcode import hls, tasks, transcoder
from app.core.transcode.capabilities import FFmpegCapabilities
from app.core.transcode.hwaccels import get_hwaccel
from app.core.transcode.hwaccels.base import (
    HDRType,
    MediaProbe,
    TranscodeContext,
    classify_hdr,
)
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


@pytest.mark.parametrize(
    ("kind", "output", "expected"),
    [
        (
            "encoders",
            "Encoders:\n V....D libx264 H.264\n A....D aac AAC\n",
            {"libx264", "aac"},
        ),
        (
            "filters",
            "Filters:\n .. scale V->V Scale\n .S tonemap V->V Tone map\n",
            {"scale", "tonemap"},
        ),
        (
            "hwaccels",
            "Hardware acceleration methods:\nvaapi\nqsv\n",
            {"vaapi", "qsv"},
        ),
        (
            "bsfs",
            "Bitstream filters:\nh264_metadata\nnull\n",
            {"h264_metadata", "null"},
        ),
        (
            "muxers",
            "Muxers:\n E  hls Apple HLS\n E  stream_segment,ssegment Segment\n",
            {"hls", "stream_segment", "ssegment"},
        ),
    ],
)
def test_parse_ffmpeg_capability_listing(kind, output, expected):
    capabilities = importlib.import_module("app.core.transcode.capabilities")

    assert capabilities._parse_listing(kind, output) == expected


def test_parse_ffmpeg_encoder_options():
    capabilities = importlib.import_module("app.core.transcode.capabilities")
    output = (
        "h264_videotoolbox AVOptions:\n"
        "  -profile <int> E..V....... Profile\n"
        "  -prio_speed <boolean> E..V....... prioritize speed\n"
    )

    assert capabilities._parse_encoder_options(output) == {"profile", "prio_speed"}


def test_load_ffmpeg_capabilities_caches_success(monkeypatch):
    outputs = {
        "-encoders": " V....D libx264 H.264\n A....D aac AAC\n",
        "-filters": " .. scale V->V Scale\n",
        "-hwaccels": "videotoolbox\n",
        "-bsfs": "h264_metadata\n",
        "-muxers": " E  hls Apple HLS\n E  mpegts MPEG-TS\n",
        "-h": "  -preset <string> E..V....... Preset\n",
    }

    async def query(_executable, *args):
        return outputs[args[0]]

    query_mock = AsyncMock(side_effect=query)
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_resolved_executable", lambda value: value)
    monkeypatch.setattr(capability_module, "_query_ffmpeg", query_mock)

    first = asyncio.run(
        capability_module.load_ffmpeg_capabilities("ffmpeg-test", "libx264")
    )
    second = asyncio.run(
        capability_module.load_ffmpeg_capabilities("ffmpeg-test", "libx264")
    )

    assert first is second
    assert first.encoders == {"libx264", "aac"}
    assert first.encoder_options == {"preset"}
    assert query_mock.await_count == 6


class _ProbeStream:
    def __init__(self, *chunks):
        self.chunks = list(chunks)

    async def read(self, size):
        del size
        return self.chunks.pop(0) if self.chunks else b""


class _ProbeProcess:
    def __init__(self, returncode=0, stderr=()):
        self.returncode = returncode
        self.stderr = _ProbeStream(*stderr)
        self.kill = Mock()

    async def wait(self):
        return self.returncode


def test_run_ffmpeg_probe_success(monkeypatch):
    proc = _ProbeProcess()
    create = AsyncMock(return_value=proc)
    monkeypatch.setattr(
        capability_module.asyncio,
        "create_subprocess_exec",
        create,
    )

    result = asyncio.run(
        capability_module._run_ffmpeg_probe(
            "ffmpeg-test",
            ["-f", "null", "-"],
        )
    )

    assert result == (True, "")
    assert create.await_args.args[:4] == (
        "ffmpeg-test",
        "-hide_banner",
        "-loglevel",
        "error",
    )
    assert create.await_args.kwargs == {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE,
    }


def test_run_ffmpeg_probe_failure_returns_bounded_stderr(monkeypatch):
    proc = _ProbeProcess(returncode=1, stderr=(b"x" * 3000,))
    monkeypatch.setattr(
        capability_module.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    success, detail = asyncio.run(
        capability_module._run_ffmpeg_probe("ffmpeg-test", [])
    )

    assert success is False
    assert len(detail) == capability_module._RUNTIME_STDERR_LIMIT


def test_run_ffmpeg_probe_allows_driver_info_after_zero_exit(monkeypatch):
    driver_info = b"libva info: va_openDriver() returns 0"
    proc = _ProbeProcess(stderr=(driver_info,))
    monkeypatch.setattr(
        capability_module.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    assert asyncio.run(capability_module._run_ffmpeg_probe("ffmpeg-test", [])) == (
        True,
        driver_info.decode(),
    )


def test_run_ffmpeg_probe_start_failure_is_reported(monkeypatch):
    monkeypatch.setattr(
        capability_module.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=OSError("unavailable")),
    )

    assert asyncio.run(capability_module._run_ffmpeg_probe("ffmpeg-test", [])) == (
        False,
        "unavailable",
    )


def test_run_ffmpeg_probe_timeout_kills_and_reaps(monkeypatch):
    stopped = asyncio.Event()

    class Process(_ProbeProcess):
        def __init__(self):
            super().__init__(returncode=None)
            self.kill = Mock(side_effect=self._stop)

        def _stop(self):
            self.returncode = -9
            stopped.set()

        async def wait(self):
            await stopped.wait()
            return self.returncode

    proc = Process()
    monkeypatch.setattr(
        capability_module.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    monkeypatch.setattr(capability_module, "_RUNTIME_PROBE_TIMEOUT", 0.01)

    success, detail = asyncio.run(
        capability_module._run_ffmpeg_probe("ffmpeg-test", [])
    )

    assert success is False
    assert detail == "timed out after 0.0 seconds"
    proc.kill.assert_called_once_with()


def test_run_ffmpeg_probe_cancellation_kills_and_reaps(monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()

    class Process(_ProbeProcess):
        def __init__(self):
            super().__init__(returncode=None)
            self.kill = Mock(side_effect=self._stop)

        def _stop(self):
            self.returncode = -9
            stopped.set()

        async def wait(self):
            started.set()
            await stopped.wait()
            return self.returncode

        async def communicate(self):
            started.set()
            await stopped.wait()
            return None, b""

    proc = Process()
    monkeypatch.setattr(
        capability_module.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    async def cancel_probe():
        task = asyncio.create_task(
            capability_module._run_ffmpeg_probe("ffmpeg-test", [])
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_probe())

    proc.kill.assert_called_once_with()
    assert proc.returncode == -9


def test_run_ffmpeg_probe_stalled_reap_remains_bounded(monkeypatch):
    never = asyncio.Event()

    class Process(_ProbeProcess):
        def __init__(self):
            super().__init__(returncode=None)

        async def wait(self):
            await never.wait()

        async def communicate(self):
            await never.wait()

    proc = Process()
    monkeypatch.setattr(
        capability_module.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    monkeypatch.setattr(capability_module, "_RUNTIME_PROBE_TIMEOUT", 0.01)
    monkeypatch.setattr(capability_module, "_RUNTIME_REAP_TIMEOUT", 0.01, raising=False)

    result = asyncio.run(
        asyncio.wait_for(
            capability_module._run_ffmpeg_probe("ffmpeg-test", []),
            timeout=0.1,
        )
    )

    assert result == (False, "timed out after 0.0 seconds")
    proc.kill.assert_called_once_with()


def test_stderr_tail_retains_only_bounded_bytes():
    stream = _ProbeStream(b"a" * 1500, b"b" * 1500)

    result = asyncio.run(capability_module._read_stderr_tail(stream))

    assert result == b"a" * 548 + b"b" * 1500


def test_hardware_encoder_probe_caches_success(monkeypatch):
    probe = AsyncMock(return_value=(True, ""))
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_run_ffmpeg_probe", probe)
    monkeypatch.setattr(
        capability_module,
        "_resolved_executable",
        lambda value: value,
    )

    for _ in range(2):
        asyncio.run(
            capability_module.require_hardware_encoder(
                "ffmpeg-test",
                "vaapi",
                "h264_vaapi",
                "/dev/dri/renderD128",
                [],
            )
        )

    probe.assert_awaited_once()


def test_hardware_encoder_probe_failure_is_not_cached(monkeypatch):
    probe = AsyncMock(return_value=(False, "device failed"))
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_run_ffmpeg_probe", probe)
    monkeypatch.setattr(
        capability_module,
        "_resolved_executable",
        lambda value: value,
    )

    for _ in range(2):
        with pytest.raises(RuntimeError, match="h264_vaapi.*device failed"):
            asyncio.run(
                capability_module.require_hardware_encoder(
                    "ffmpeg-test",
                    "vaapi",
                    "h264_vaapi",
                    None,
                    [],
                )
            )

    assert probe.await_count == 2


def test_hardware_decode_probe_cache_tracks_file_state(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"first")
    probe = AsyncMock(return_value=(True, ""))
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_run_ffmpeg_probe", probe)
    monkeypatch.setattr(
        capability_module,
        "_resolved_executable",
        lambda value: value,
    )

    for _ in range(2):
        assert (
            asyncio.run(
                capability_module.probe_hardware_decode(
                    "ffmpeg-test",
                    "vaapi",
                    None,
                    str(media),
                    0,
                    [],
                )
            )
            is True
        )
    media.write_bytes(b"changed-size")
    assert (
        asyncio.run(
            capability_module.probe_hardware_decode(
                "ffmpeg-test",
                "vaapi",
                None,
                str(media),
                0,
                [],
            )
        )
        is True
    )

    assert probe.await_count == 2


def test_hardware_decode_probe_failure_is_not_cached(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    probe = AsyncMock(return_value=(False, "decode failed"))
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_run_ffmpeg_probe", probe)
    monkeypatch.setattr(
        capability_module,
        "_resolved_executable",
        lambda value: value,
    )

    for _ in range(2):
        assert (
            asyncio.run(
                capability_module.probe_hardware_decode(
                    "ffmpeg-test",
                    "vaapi",
                    None,
                    str(media),
                    0,
                    [],
                )
            )
            is False
        )

    assert probe.await_count == 2


def test_hardware_transform_probe_cache_tracks_signature_and_file_state(
    monkeypatch, tmp_path
):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"first")
    probe = AsyncMock(return_value=(True, ""))
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_run_ffmpeg_probe", probe)
    monkeypatch.setattr(
        capability_module,
        "_resolved_executable",
        lambda value: value,
    )

    async def run(signature):
        return await capability_module.probe_hardware_transform(
            "ffmpeg-test",
            "vaapi",
            "/dev/dri/renderD128",
            str(media),
            0,
            signature,
            [],
        )

    assert asyncio.run(run("transpose_vaapi=dir=clock")) is True
    assert asyncio.run(run("transpose_vaapi=dir=clock")) is True
    assert asyncio.run(run("transpose_vaapi=dir=cclock")) is True
    media.write_bytes(b"changed-size")
    assert asyncio.run(run("transpose_vaapi=dir=clock")) is True

    assert probe.await_count == 3


def test_hardware_transform_probe_failure_is_not_cached(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    probe = AsyncMock(return_value=(False, "filter failed"))
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_run_ffmpeg_probe", probe)
    monkeypatch.setattr(
        capability_module,
        "_resolved_executable",
        lambda value: value,
    )

    for _ in range(2):
        assert (
            asyncio.run(
                capability_module.probe_hardware_transform(
                    "ffmpeg-test",
                    "qsv",
                    "/dev/dri/renderD128",
                    str(media),
                    0,
                    "vpp_qsv=deinterlace=advanced:rate=frame",
                    [],
                )
            )
            is False
        )

    assert probe.await_count == 2


def test_clear_ffmpeg_capability_cache_clears_runtime_successes(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    probe = AsyncMock(return_value=(True, ""))
    capability_module.clear_ffmpeg_capability_cache()
    monkeypatch.setattr(capability_module, "_run_ffmpeg_probe", probe)
    monkeypatch.setattr(
        capability_module,
        "_resolved_executable",
        lambda value: value,
    )

    async def run_probes():
        await capability_module.require_hardware_encoder(
            "ffmpeg-test",
            "vaapi",
            "h264_vaapi",
            None,
            [],
        )
        await capability_module.probe_hardware_decode(
            "ffmpeg-test",
            "vaapi",
            None,
            str(media),
            0,
            [],
        )
        await capability_module.probe_hardware_transform(
            "ffmpeg-test",
            "vaapi",
            None,
            str(media),
            0,
            "transpose_vaapi=dir=clock",
            [],
        )

    asyncio.run(run_probes())
    capability_module.clear_ffmpeg_capability_cache()
    asyncio.run(run_probes())

    assert probe.await_count == 6


class _Lock:
    def __init__(self):
        self.locked = False

    def acquire(self):
        self.locked = True

    def release(self):
        self.locked = False


def _runtime_task(out_dir, state=tasks.TaskState.RUNNING):
    return {
        "id": "hash:profile",
        "name": "input.mkv",
        "path": "/media/input.mkv",
        "hash": "hash",
        "state": state,
        "duration": 60.0,
        "pid": 123,
        "process_start_id": "original-process",
        "profile": "profile",
        "quality": "medium",
        "resolution": "original",
        "hwaccel": None,
        "out_dir": str(out_dir),
        "started_at": "2026-01-01",
        "finished_at": None,
        "error_msg": None,
    }


def test_register_stores_process_start_id(monkeypatch, tmp_path):
    store = {}
    process_start_id = AsyncMock(return_value="process-start-id")
    proc = SimpleNamespace(pid=123)

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(tasks, "_process_start_id", process_start_id)

    task_id = asyncio.run(
        tasks.register_task(
            "/media/input.mkv",
            "hash",
            TranscodeOptions(),
            tmp_path,
            proc,
            60.0,
        )
    )

    assert store[task_id]["process_start_id"] == "process-start-id"
    process_start_id.assert_awaited_once_with(123)


def test_reads_linux_process_start_id(monkeypatch):
    fields = ["S", *(str(field) for field in range(4, 23))]
    stat = f"123 (ffmpeg worker) {' '.join(fields)}"

    monkeypatch.setattr(tasks.sys, "platform", "linux")
    monkeypatch.setattr(tasks.Path, "read_text", lambda *_args, **_kwargs: stat)

    assert tasks._read_process_start_id(123) == "linux:22"


def test_reads_macos_process_start_id(monkeypatch):
    run = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="Sun Jul 12 12:34:56 2026\n",
        )
    )

    monkeypatch.setattr(tasks.sys, "platform", "darwin")
    monkeypatch.setattr(tasks.subprocess, "run", run)

    assert tasks._read_process_start_id(123) == ("darwin:Sun Jul 12 12:34:56 2026")
    run.assert_called_once_with(
        ["ps", "-o", "lstart=", "-p", "123"],
        capture_output=True,
        check=False,
        text=True,
        timeout=2,
    )


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


def test_scan_outputs(tmp_path):
    finished = tmp_path / "hash-a" / "high_720p_nvenc"
    stopped = tmp_path / "hash-b" / "medium_original_none"
    empty = tmp_path / "hash-c" / "low_480p_vaapi"
    finished.mkdir(parents=True)
    stopped.mkdir(parents=True)
    empty.mkdir(parents=True)
    (finished / "index.m3u8").write_text(
        "#EXTM3U\n#EXTINF:6.0,\nsegment_000000.ts\n#EXT-X-ENDLIST\n"
    )
    (stopped / "index.m3u8").write_text("#EXTM3U\n#EXTINF:3.0,\nsegment_000000.ts\n")

    result = {task["id"]: task for task in hls.scan_outputs(tmp_path)}

    assert set(result) == {
        "hash-a:high_720p_nvenc",
        "hash-b:medium_original_none",
    }
    assert result["hash-a:high_720p_nvenc"]["state"] == tasks.TaskState.FINISHED
    assert result["hash-a:high_720p_nvenc"]["duration"] == 6.0
    assert result["hash-b:medium_original_none"]["state"] == tasks.TaskState.STOPPED
    assert result["hash-b:medium_original_none"]["encoded_duration"] == 3.0


def test_delete_output(tmp_path):
    root = tmp_path / "transcoded"
    out_dir = root / "hash" / "profile"
    out_dir.mkdir(parents=True)
    (out_dir / "index.m3u8").write_text("#EXTM3U\n")

    assert hls.delete_output("hash", "profile", root=root) is True
    assert not out_dir.exists()
    assert not out_dir.parent.exists()


def test_lock_name(tmp_path):
    out_dir = tmp_path / "transcoded" / "hash" / "medium_720p_none"
    lock = hls.acquire_output_lock(out_dir)
    assert lock is not None

    try:
        assert Path(lock.lock_file).name == "hash_medium_720p_none.lock"
    finally:
        lock.release()


def test_delete_locked(tmp_path):
    root = tmp_path / "transcoded"
    out_dir = root / "hash" / "profile"
    out_dir.mkdir(parents=True)
    playlist = out_dir / "index.m3u8"
    playlist.write_text("#EXTM3U\n")
    lock = transcoder._acquire_lock(out_dir)
    assert lock is not None

    try:
        assert hls.delete_output("hash", "profile", root=root) is False
    finally:
        transcoder._release_lock(lock)

    assert playlist.is_file()


def test_delete_escape(tmp_path):
    root = tmp_path / "transcoded"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    assert hls.delete_output("..", "outside", root=root) is False
    assert outside.is_dir()


def test_read_fallback(tmp_path):
    playlist = tmp_path / "profile" / "index.m3u8"
    playlist.parent.mkdir()

    assert asyncio.run(hls.read_m3u8(playlist)) == hls._MINIMAL_M3U8
    playlist.write_text("")
    assert asyncio.run(hls.read_m3u8(playlist)) == hls._MINIMAL_M3U8
    playlist.write_text("#EXTM3U\n")
    assert asyncio.run(hls.read_m3u8(playlist)) == "#EXTM3U\n"


def test_read_missing(tmp_path):
    assert asyncio.run(hls.read_m3u8(tmp_path / "missing" / "index.m3u8")) is None


def test_wait_segment(tmp_path):
    playlist = tmp_path / "index.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:6.0,\nsegment_000000.ts\n")

    assert asyncio.run(hls.wait_segment(playlist, timeout=0.01, interval=0)) is True


def test_wait_exited(tmp_path):
    proc = SimpleNamespace(returncode=1)

    result = asyncio.run(
        hls.wait_segment(
            tmp_path / "index.m3u8",
            proc=cast(asyncio.subprocess.Process, proc),
            timeout=1,
            interval=0.1,
        )
    )

    assert result is False


def test_probe_media(monkeypatch):
    probe_proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                b'{"streams":[{"index":0,"codec_type":"video",'
                b'"disposition":{"attached_pic":1},"height":600},'
                b'{"index":1,"codec_type":"audio"},'
                b'{"index":2,"codec_type":"video",'
                b'"codec_name":"hevc","profile":"Main 10",'
                b'"bits_per_raw_sample":"10","bits_per_sample":0,'
                b'"disposition":{"attached_pic":0},'
                b'"avg_frame_rate":"30000/1001",'
                b'"r_frame_rate":"30000/1001",'
                b'"pix_fmt":"yuv420p10le","width":1920,"height":1080,'
                b'"sample_aspect_ratio":"4:3","field_order":"tt",'
                b'"color_range":"tv",'
                b'"color_transfer":"smpte2084","color_primaries":"bt2020",'
                b'"color_space":"bt2020nc",'
                b'"side_data_list":[{"side_data_type":"Display Matrix",'
                b'"rotation":-90},{"side_data_type":"DOVI configuration record",'
                b'"dv_profile":8,"bl_present_flag":1,'
                b'"dv_bl_signal_compatibility_id":1}]}],'
                b'"format":{"duration":"60.5"}}',
                b"",
            )
        ),
    )
    frame_proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                b'{"frames":[{"side_data_list":['
                b'{"side_data_type":"HDR Dynamic Metadata SMPTE2094-40 '
                b'(HDR10+)"}]}]}',
                b"",
            )
        ),
    )
    create = AsyncMock(side_effect=[probe_proc, frame_proc])

    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(transcoder.asyncio, "create_subprocess_exec", create)

    result = asyncio.run(transcoder._probe_media("input.mkv"))

    assert result == MediaProbe(
        video_stream_index=2,
        audio_stream_index=1,
        duration=60.5,
        avg_frame_rate=Fraction(30000, 1001),
        r_frame_rate=Fraction(30000, 1001),
        pixel_format="yuv420p10le",
        width=1920,
        height=1080,
        sample_aspect_ratio=(4, 3),
        rotation=90,
        field_order="tt",
        color_transfer="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
        codec="hevc",
        profile="Main 10",
        bit_depth=10,
        color_range="tv",
        dovi_profile=8,
        dovi_bl_present=True,
        dovi_bl_signal_compatibility_id=1,
        hdr10_plus=True,
    )
    assert create.await_count == 2
    await_args = create.await_args_list[0]
    assert await_args is not None
    args = await_args.args
    assert (
        "format=duration:stream=index,codec_type,codec_name,profile,"
        "bits_per_sample,bits_per_raw_sample,avg_frame_rate,r_frame_rate,"
        "pix_fmt,width,height,"
        "sample_aspect_ratio,field_order,"
        "color_range,color_transfer,color_primaries,color_space:"
        "stream_disposition=attached_pic:stream_side_data=side_data_type,"
        "rotation,dv_profile,bl_present_flag,dv_bl_signal_compatibility_id"
    ) in args
    assert "-select_streams" not in args
    assert "json" in args

    frame_args = create.await_args_list[1].args
    assert frame_args[frame_args.index("-select_streams") + 1] == "2"
    assert frame_args[frame_args.index("-read_intervals") + 1] == "%+#1"
    assert "-show_frames" in frame_args
    assert (
        frame_args[frame_args.index("-show_entries") + 1]
        == "frame=stream_index:frame_side_data=side_data_type"
    )


@pytest.mark.parametrize(
    ("pixel_format", "expected"),
    [
        ("yuv420p", 8),
        ("nv12", 8),
        ("yuv420p10le", 10),
        ("p010le", 10),
        ("p012le", 12),
        ("unknown", None),
        (None, None),
    ],
)
def test_pixel_format_bit_depth(pixel_format, expected):
    assert transcoder._pixel_format_bit_depth(pixel_format) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1:1", (1, 1)),
        ("16/15", (16, 15)),
        (" 4:3 ", (4, 3)),
        ("0:1", None),
        ("1:0", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_parse_sample_aspect_ratio(value, expected):
    assert transcoder._parse_sample_aspect_ratio(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30000/1001", Fraction(30000, 1001)),
        ("50/2", Fraction(25, 1)),
        ("25", Fraction(25, 1)),
        ("0/0", None),
        ("0/1", None),
        ("-24/1", None),
        ("bad", None),
        (None, None),
        (24, None),
    ],
)
def test_parse_frame_rate(value, expected):
    assert transcoder._parse_frame_rate(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (-90, 90),
        (180, 180),
        (90, 270),
        (-359.95, 0),
        (-45, 45),
        ("bad", None),
        (None, None),
    ],
)
def test_parse_rotation(value, expected):
    assert transcoder._parse_rotation(value) == expected


@pytest.mark.parametrize(
    ("raw_fields", "expected"),
    [
        ('"bits_per_raw_sample":"12","bits_per_sample":10', 12),
        ('"bits_per_raw_sample":"0","bits_per_sample":10', 10),
        ('"bits_per_raw_sample":"N/A","pix_fmt":"p010le"', 10),
    ],
)
def test_probe_media_bit_depth_precedence(monkeypatch, raw_fields, expected):
    stdout = (
        '{"streams":[{"index":0,"codec_type":"video",'
        f"{raw_fields}" + '}],"format":{"duration":"1"}}'
    ).encode()
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(stdout, b"")),
    )
    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(
        transcoder.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    assert asyncio.run(transcoder._probe_media("input.mkv")).bit_depth == expected


def test_probe_hdr10_plus_detects_dynamic_metadata(monkeypatch):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                b'{"frames":[{"side_data_list":['
                b'{"side_data_type":"HDR Dynamic Metadata SMPTE2094-40 '
                b'(HDR10+)"}]}]}',
                b"",
            )
        ),
    )
    create = AsyncMock(return_value=proc)
    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(transcoder.asyncio, "create_subprocess_exec", create)

    assert asyncio.run(transcoder._probe_hdr10_plus("input.mkv", 2)) is True


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, b""),
        (0, b'{"frames":[{"side_data_list":[]}]}'),
        (0, b"invalid"),
    ],
)
def test_probe_hdr10_plus_returns_false_without_metadata(
    monkeypatch, returncode, stdout
):
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

    assert asyncio.run(transcoder._probe_hdr10_plus("input.mkv", 2)) is False


def test_probe_hdr10_plus_timeout_kills_and_reaps(monkeypatch):
    proc = SimpleNamespace(
        returncode=None,
        kill=Mock(),
        communicate=AsyncMock(side_effect=[TimeoutError, (b"", b"")]),
    )
    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(
        transcoder.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    assert asyncio.run(transcoder._probe_hdr10_plus("input.mkv", 2)) is False
    proc.kill.assert_called_once_with()
    assert proc.communicate.await_count == 2


def test_probe_hdr10_plus_start_failure_is_optional(monkeypatch):
    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(
        transcoder.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=OSError("unavailable")),
    )

    assert asyncio.run(transcoder._probe_hdr10_plus("input.mkv", 2)) is False


def test_probe_media_skips_hdr10_plus_for_non_pq_candidate(monkeypatch):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                b'{"streams":[{"index":0,"codec_type":"video",'
                b'"bits_per_raw_sample":"8","color_transfer":"bt709"}]}',
                b"",
            )
        ),
    )
    hdr10_plus_probe = AsyncMock(return_value=True)
    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(
        transcoder.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    monkeypatch.setattr(transcoder, "_probe_hdr10_plus", hdr10_plus_probe)

    result = asyncio.run(transcoder._probe_media("input.mkv"))

    assert result.hdr10_plus is False
    hdr10_plus_probe.assert_not_awaited()


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (1, b"", MediaProbe()),
        (
            0,
            b'{"streams":[{"index":0,"codec_type":"video",'
            b'"avg_frame_rate":"bad"}],"format":{"duration":"60"}}',
            MediaProbe(video_stream_index=0, duration=60.0),
        ),
        (
            0,
            b'{"streams":[{"index":0,"codec_type":"video",'
            b'"avg_frame_rate":"24/1"}],"format":{"duration":"bad"}}',
            MediaProbe(video_stream_index=0, avg_frame_rate=Fraction(24, 1)),
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


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(Fraction(30000, 1001), 30000 / 1001), (None, None)],
)
def test_probe_framerate_converts_average_at_api_boundary(monkeypatch, rate, expected):
    monkeypatch.setattr(
        transcoder,
        "_probe_media",
        AsyncMock(return_value=MediaProbe(avg_frame_rate=rate)),
    )

    assert asyncio.run(transcoder.probe_framerate("input.mkv")) == expected


def test_probe_timeout(monkeypatch):
    proc = SimpleNamespace(
        returncode=None,
        kill=Mock(),
        communicate=AsyncMock(side_effect=[TimeoutError, (b"", b"")]),
    )

    monkeypatch.setattr(transcoder, "_ffprobe", AsyncMock(return_value="ffprobe"))
    monkeypatch.setattr(
        transcoder.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )

    assert asyncio.run(transcoder._probe_media("input.mkv")) == MediaProbe()
    proc.kill.assert_called_once_with()
    assert proc.communicate.await_count == 2


def test_transcode_context():
    options = TranscodeOptions(
        hwaccel="nvenc",
        quality="high",
        resolution="720p",
    )
    metadata = MediaProbe(
        avg_frame_rate=Fraction(47, 2),
        r_frame_rate=Fraction(47, 2),
        pixel_format="yuv420p10le",
        width=1920,
        height=1080,
    )
    context = TranscodeContext(options=options, metadata=metadata)

    assert context.options is options
    assert context.metadata is metadata
    assert context.source_framerate == Fraction(47, 2)
    assert context.has_stable_framerate is True
    assert context.fixed_gop_size == 141
    assert context.source_pixel_format == "yuv420p10le"
    assert context.source_height == 1080
    assert options.segment_length == 6
    assert "segment_length" not in {field.name for field in fields(TranscodeOptions)}
    assert context.needs_scale is True
    assert context.scale_height == "720"
    assert context.scale_width == "1280"
    assert context.encoder_config is options.encoder_config


@pytest.mark.parametrize(
    ("avg_frame_rate", "r_frame_rate", "expected_stable", "expected_gop"),
    [
        (Fraction(24000, 1001), Fraction(24), True, 144),
        (Fraction(30000, 1001), Fraction(30000, 1001), True, 180),
        (Fraction(3003, 125), Fraction(24), True, 145),
        (Fraction(961, 40), Fraction(24), False, None),
        (Fraction(6075, 271), Fraction(15), False, None),
        (Fraction(24), None, False, None),
        (None, Fraction(24), False, None),
    ],
)
def test_transcode_context_frame_rate_stability(
    avg_frame_rate, r_frame_rate, expected_stable, expected_gop
):
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(
            avg_frame_rate=avg_frame_rate,
            r_frame_rate=r_frame_rate,
        ),
    )

    assert context.has_stable_framerate is expected_stable
    assert context.fixed_gop_size == expected_gop


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            MediaProbe(
                codec="h264",
                profile="High",
                bit_depth=8,
                pixel_format="yuv420p",
            ),
            True,
        ),
        (
            MediaProbe(
                codec="avc",
                profile="Constrained Baseline",
                bit_depth=8,
                pixel_format="nv12",
            ),
            True,
        ),
        (
            MediaProbe(
                codec="hevc",
                profile="Main",
                bit_depth=8,
                pixel_format="yuv420p",
            ),
            True,
        ),
        (
            MediaProbe(
                codec="hevc",
                profile="Main 10",
                bit_depth=10,
                pixel_format="yuv420p10le",
            ),
            True,
        ),
        (
            MediaProbe(
                codec="h264",
                profile="High 10",
                bit_depth=10,
                pixel_format="yuv420p10le",
            ),
            False,
        ),
        (
            MediaProbe(
                codec="hevc",
                profile="Main 10",
                bit_depth=12,
                pixel_format="yuv420p12le",
            ),
            False,
        ),
        (
            MediaProbe(
                codec="hevc",
                profile="Main 10",
                bit_depth=10,
                pixel_format="yuv422p10le",
            ),
            False,
        ),
        (
            MediaProbe(
                codec="hevc",
                profile="Main 10",
                bit_depth=10,
                pixel_format="yuv444p10le",
            ),
            False,
        ),
        (
            MediaProbe(
                codec="vp9",
                profile="Profile 0",
                bit_depth=8,
                pixel_format="yuv420p",
            ),
            False,
        ),
        (
            MediaProbe(
                codec="h264",
                bit_depth=8,
                pixel_format="yuv420p",
            ),
            False,
        ),
        (
            MediaProbe(
                codec="h264",
                profile="High!",
                bit_depth=8,
                pixel_format="yuv420p",
            ),
            False,
        ),
        (
            MediaProbe(
                codec="hevc",
                profile="Main.10",
                bit_depth=10,
                pixel_format="p010le",
            ),
            False,
        ),
    ],
)
def test_hardware_decode_candidate(metadata, expected):
    assert base_module._is_decode_candidate(metadata) is expected


def test_context_uses_prepared_hardware_decode_result():
    options = TranscodeOptions(hwaccel="vaapi")
    capabilities = _capabilities(hwaccels=("vaapi",))

    enabled = TranscodeContext(
        options=options,
        capabilities=capabilities,
        hardware=base_module.HardwareRuntime("/dev/dri/renderD128", True),
    )
    disabled = TranscodeContext(
        options=options,
        capabilities=capabilities,
        hardware=base_module.HardwareRuntime("/dev/dri/renderD128", False),
    )
    unprepared = TranscodeContext(options=options, capabilities=capabilities)

    assert enabled.uses_hw_decode is True
    assert disabled.uses_hw_decode is False
    assert unprepared.uses_hw_decode is True


def _eligible_hardware_context(hwaccel, *, capabilities=None, resolution="original"):
    return TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel, resolution=resolution),
        metadata=MediaProbe(
            video_stream_index=2,
            codec="h264",
            profile="High",
            bit_depth=8,
            pixel_format="yuv420p",
            height=1080,
        ),
        capabilities=capabilities or _capabilities(),
    )


def test_software_prepare_hardware_is_noop():
    context = TranscodeContext(options=TranscodeOptions())

    assert asyncio.run(get_hwaccel(None).prepare_hardware(context, "input.mkv")) is None


def test_videotoolbox_prepare_hardware_uses_null_probes(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    require_encoder = AsyncMock()
    probe_decode = AsyncMock(return_value=True)
    monkeypatch.setattr(
        base_module,
        "require_hardware_encoder",
        require_encoder,
        raising=False,
    )
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        probe_decode,
        raising=False,
    )
    context = _eligible_hardware_context("videotoolbox")

    runtime = asyncio.run(
        get_hwaccel("videotoolbox").prepare_hardware(context, str(media))
    )

    assert runtime == base_module.HardwareRuntime(None, True)
    encoder_args = require_encoder.await_args.args[-1]
    assert encoder_args[-2:] == ["null", "-"]
    assert "h264_videotoolbox" in encoder_args
    decode_args = probe_decode.await_args.args[-1]
    assert decode_args[decode_args.index("-map") + 1] == "0:2"
    assert decode_args[decode_args.index("-vf") + 1] == "hwdownload,format=nv12"
    assert decode_args[-2:] == ["null", "-"]
    assert "videotoolbox" in decode_args


def test_prepare_hardware_downloads_10_bit_probe_frame(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    monkeypatch.setattr(
        base_module,
        "require_hardware_encoder",
        AsyncMock(),
        raising=False,
    )
    probe_decode = AsyncMock(return_value=True)
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        probe_decode,
        raising=False,
    )
    context = _eligible_hardware_context("videotoolbox")
    context.metadata = replace(
        context.metadata,
        bit_depth=10,
        codec="hevc",
        profile="Main 10",
        pixel_format="yuv420p10le",
    )

    asyncio.run(get_hwaccel("videotoolbox").prepare_hardware(context, str(media)))

    decode_args = probe_decode.await_args.args[-1]
    assert decode_args[decode_args.index("-vf") + 1] == "hwdownload,format=p010le"


def test_nvenc_prepare_hardware_decode_failure_keeps_encoder(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    require_encoder = AsyncMock()
    probe_decode = AsyncMock(return_value=False)
    monkeypatch.setattr(
        base_module,
        "require_hardware_encoder",
        require_encoder,
        raising=False,
    )
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        probe_decode,
        raising=False,
    )
    context = _eligible_hardware_context("nvenc")

    runtime = asyncio.run(get_hwaccel("nvenc").prepare_hardware(context, str(media)))

    assert runtime == base_module.HardwareRuntime("0", False)
    require_encoder.assert_awaited_once()
    assert "h264_nvenc" in require_encoder.await_args.args[-1]
    assert "cuda" in probe_decode.await_args.args[-1]


def test_prepare_hardware_probes_source_transform_graph(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel("nvenc")
    require_encoder = AsyncMock()
    probe_decode = AsyncMock(return_value=True)
    probe_transform = AsyncMock(return_value=True)
    monkeypatch.setattr(base_module, "require_hardware_encoder", require_encoder)
    monkeypatch.setattr(base_module, "probe_hardware_decode", probe_decode)
    monkeypatch.setattr(
        base_module,
        "probe_hardware_transform",
        probe_transform,
        raising=False,
    )
    monkeypatch.setattr(
        strategy,
        "transform_filters",
        Mock(return_value=["transpose_cuda=dir=clock"]),
        raising=False,
    )
    monkeypatch.setattr(
        strategy,
        "transform_filter_names",
        Mock(return_value={"transpose_cuda"}),
        raising=False,
    )
    context = _eligible_hardware_context(
        "nvenc",
        capabilities=_capabilities(
            filters=(*_capabilities().filters, "transpose_cuda")
        ),
    )
    context.metadata = replace(
        context.metadata,
        width=1920,
        height=1080,
        rotation=90,
    )

    runtime = asyncio.run(strategy.prepare_hardware(context, str(media)))

    assert runtime == base_module.HardwareRuntime("0", True, True)
    transform_args = probe_transform.await_args.args[-1]
    assert transform_args[transform_args.index("-noautorotate") + 1] == "-i"
    assert transform_args[transform_args.index("-map") + 1] == "0:2"
    assert transform_args[transform_args.index("-vf") + 1] == (
        "transpose_cuda=dir=clock,hwdownload,format=nv12"
    )
    assert transform_args[-2:] == ["null", "-"]
    assert probe_transform.await_args.args[-2] == "transpose_cuda=dir=clock"


def test_prepare_hardware_missing_transform_filter_uses_cpu(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel("nvenc")
    monkeypatch.setattr(base_module, "require_hardware_encoder", AsyncMock())
    monkeypatch.setattr(
        base_module, "probe_hardware_decode", AsyncMock(return_value=True)
    )
    probe_transform = AsyncMock(return_value=True)
    monkeypatch.setattr(
        base_module,
        "probe_hardware_transform",
        probe_transform,
        raising=False,
    )
    monkeypatch.setattr(
        strategy,
        "transform_filters",
        Mock(return_value=["transpose_cuda=dir=clock"]),
        raising=False,
    )
    monkeypatch.setattr(
        strategy,
        "transform_filter_names",
        Mock(return_value={"transpose_cuda"}),
        raising=False,
    )
    context = _eligible_hardware_context("nvenc")

    runtime = asyncio.run(strategy.prepare_hardware(context, str(media)))

    assert runtime == base_module.HardwareRuntime("0", True, False)
    probe_transform.assert_not_awaited()


@pytest.mark.parametrize(
    (
        "hwaccel",
        "device",
        "input_hwaccel",
        "output_format",
        "probe_download",
        "encoder",
        "expected_filter",
    ),
    [
        (
            "nvenc",
            "0",
            "cuda",
            "cuda",
            "yuv420p",
            "h264_nvenc",
            "scale_cuda=w=1280:h=720:format=yuv420p,setsar=1",
        ),
        (
            "vaapi",
            "/dev/dri/renderD128",
            "vaapi",
            "vaapi",
            "nv12",
            "h264_vaapi",
            "scale_vaapi=w=1280:h=720:format=nv12,setsar=1",
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            "qsv",
            "qsv",
            "nv12",
            "h264_qsv",
            "vpp_qsv=w=1280:h=720:format=nv12,setsar=1",
        ),
        (
            "videotoolbox",
            None,
            "videotoolbox",
            "videotoolbox_vld",
            "nv12",
            "h264_videotoolbox",
            "scale_vt=w=1280:h=720,setsar=1",
        ),
    ],
)
def test_scaled_transform_success_builds_hardware_frame_command(
    monkeypatch,
    tmp_path,
    hwaccel,
    device,
    input_hwaccel,
    output_format,
    probe_download,
    encoder,
    expected_filter,
):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel(hwaccel)
    monkeypatch.setattr(base_module, "require_hardware_encoder", AsyncMock())
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        AsyncMock(return_value=True),
    )
    probe_transform = AsyncMock(return_value=True)
    monkeypatch.setattr(base_module, "probe_hardware_transform", probe_transform)
    monkeypatch.setattr(
        strategy,
        "resolve_device",
        AsyncMock(return_value=device),
    )
    context = _eligible_hardware_context(hwaccel, resolution="720p")
    context.metadata = replace(
        context.metadata,
        width=1920,
        height=1080,
        field_order="progressive",
    )

    context.hardware = asyncio.run(strategy.prepare_hardware(context, str(media)))
    cmd = asyncio.run(transcoder._build_hls_cmd(str(media), tmp_path, context))

    assert context.hardware == base_module.HardwareRuntime(device, True, True)
    transform_args = probe_transform.await_args.args[-1]
    assert transform_args[transform_args.index("-hwaccel_output_format") + 1] == (
        output_format
    )
    assert transform_args[transform_args.index("-vf") + 1] == (
        f"{expected_filter},hwdownload,format={probe_download}"
    )
    assert cmd[cmd.index("-hwaccel") + 1] == input_hwaccel
    assert cmd[cmd.index("-hwaccel_output_format") + 1] == output_format
    assert cmd[cmd.index("-c:v") + 1] == encoder
    vf = cmd[cmd.index("-vf") + 1]
    assert vf == expected_filter
    assert "scale=" not in vf
    assert "hwdownload" not in vf
    assert "hwupload" not in vf


@pytest.mark.parametrize(
    ("hwaccel", "device", "expected_download"),
    [
        ("nvenc", "0", "yuv420p"),
        ("vaapi", "/dev/dri/renderD128", "nv12"),
        ("qsv", "/dev/dri/renderD128", "nv12"),
        ("videotoolbox", None, "p010le"),
    ],
)
def test_ten_bit_sdr_scale_probe_uses_transform_output_format(
    monkeypatch,
    tmp_path,
    hwaccel,
    device,
    expected_download,
):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel(hwaccel)
    monkeypatch.setattr(base_module, "require_hardware_encoder", AsyncMock())
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        AsyncMock(return_value=True),
    )
    probe_transform = AsyncMock(return_value=True)
    monkeypatch.setattr(base_module, "probe_hardware_transform", probe_transform)
    monkeypatch.setattr(
        strategy,
        "resolve_device",
        AsyncMock(return_value=device),
    )
    context = _eligible_hardware_context(hwaccel, resolution="720p")
    context.metadata = replace(
        context.metadata,
        codec="hevc",
        profile="Main 10",
        bit_depth=10,
        pixel_format="p010le",
        width=1920,
        height=1080,
    )

    asyncio.run(strategy.prepare_hardware(context, str(media)))

    transform_args = probe_transform.await_args.args[-1]
    assert transform_args[transform_args.index("-vf") + 1].endswith(
        f"hwdownload,format={expected_download}"
    )


@pytest.mark.parametrize("fallback", ["missing_filter", "runtime_failure"])
@pytest.mark.parametrize(
    (
        "hwaccel",
        "device",
        "scaler",
        "expected_hwaccel",
        "encoder",
        "expected_filter",
        "expected_decode",
    ),
    [
        (
            "nvenc",
            "0",
            "scale_cuda",
            "cuda",
            "h264_nvenc",
            "scale=1280:720,setsar=1,format=yuv420p",
            True,
        ),
        (
            "vaapi",
            "/dev/dri/renderD128",
            "scale_vaapi",
            "vaapi",
            "h264_vaapi",
            "scale=1280:720,setsar=1,format=nv12,hwupload",
            True,
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            "vpp_qsv",
            None,
            "h264_qsv",
            "scale=1280:720,setsar=1,format=nv12",
            False,
        ),
        (
            "videotoolbox",
            None,
            "scale_vt",
            "videotoolbox",
            "h264_videotoolbox",
            "scale=1280:720,setsar=1,format=nv12",
            True,
        ),
    ],
)
def test_scaled_transform_fallback_builds_cpu_scale_command(
    monkeypatch,
    tmp_path,
    fallback,
    hwaccel,
    device,
    scaler,
    expected_hwaccel,
    encoder,
    expected_filter,
    expected_decode,
):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel(hwaccel)
    monkeypatch.setattr(base_module, "require_hardware_encoder", AsyncMock())
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        AsyncMock(return_value=True),
    )
    probe_transform = AsyncMock(return_value=False)
    monkeypatch.setattr(base_module, "probe_hardware_transform", probe_transform)
    monkeypatch.setattr(
        strategy,
        "resolve_device",
        AsyncMock(return_value=device),
    )
    capabilities = _capabilities()
    if fallback == "missing_filter":
        capabilities = _capabilities(filters=capabilities.filters - {scaler})
    context = _eligible_hardware_context(
        hwaccel,
        capabilities=capabilities,
        resolution="720p",
    )
    context.metadata = replace(context.metadata, width=1920, height=1080)

    context.hardware = asyncio.run(strategy.prepare_hardware(context, str(media)))
    cmd = asyncio.run(transcoder._build_hls_cmd(str(media), tmp_path, context))

    assert context.hardware == base_module.HardwareRuntime(
        device,
        expected_decode,
        False,
    )
    assert "-hwaccel_output_format" not in cmd
    if expected_hwaccel is None:
        assert "-hwaccel" not in cmd
    else:
        assert cmd[cmd.index("-hwaccel") + 1] == expected_hwaccel
    assert cmd[cmd.index("-c:v") + 1] == encoder
    assert cmd[cmd.index("-vf") + 1] == expected_filter
    if fallback == "missing_filter":
        probe_transform.assert_not_awaited()
    else:
        probe_transform.assert_awaited_once()


@pytest.mark.parametrize(
    ("hwaccel", "device", "encoder", "expected_filter"),
    [
        (
            "nvenc",
            "0",
            "h264_nvenc",
            "scale=1280:720,setsar=1,format=yuv420p",
        ),
        (
            "vaapi",
            "/dev/dri/renderD128",
            "h264_vaapi",
            "scale=1280:720,setsar=1,format=nv12,hwupload",
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            "h264_qsv",
            "scale=1280:720,setsar=1,format=nv12",
        ),
        (
            "videotoolbox",
            None,
            "h264_videotoolbox",
            "scale=1280:720,setsar=1,format=nv12",
        ),
    ],
)
def test_scaled_unsupported_source_keeps_hardware_encoder(
    monkeypatch,
    tmp_path,
    hwaccel,
    device,
    encoder,
    expected_filter,
):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel(hwaccel)
    monkeypatch.setattr(base_module, "require_hardware_encoder", AsyncMock())
    probe_decode = AsyncMock(return_value=True)
    probe_transform = AsyncMock(return_value=True)
    monkeypatch.setattr(base_module, "probe_hardware_decode", probe_decode)
    monkeypatch.setattr(base_module, "probe_hardware_transform", probe_transform)
    monkeypatch.setattr(
        strategy,
        "resolve_device",
        AsyncMock(return_value=device),
    )
    context = _eligible_hardware_context(hwaccel, resolution="720p")
    context.metadata = replace(
        context.metadata,
        codec="vp9",
        profile="Profile 0",
        bit_depth=8,
        pixel_format="yuv420p",
        width=1920,
        height=1080,
    )

    context.hardware = asyncio.run(strategy.prepare_hardware(context, str(media)))
    cmd = asyncio.run(transcoder._build_hls_cmd(str(media), tmp_path, context))

    assert context.hardware == base_module.HardwareRuntime(device, False, False)
    probe_decode.assert_not_awaited()
    probe_transform.assert_not_awaited()
    assert cmd[cmd.index("-c:v") + 1] == encoder
    assert cmd[cmd.index("-vf") + 1] == expected_filter


@pytest.mark.parametrize("fallback", ["missing_filter", "runtime_failure"])
@pytest.mark.parametrize(
    (
        "hwaccel",
        "device",
        "encoder",
        "field_order",
        "rotation",
        "hardware_filter",
        "expected_hwaccel",
        "expected_filter",
        "expected_decode",
    ),
    [
        (
            "nvenc",
            "0",
            "h264_nvenc",
            "tt",
            0,
            "yadif_cuda",
            "cuda",
            ("bwdif=mode=send_frame:parity=tff:deint=all,setfield=prog,format=yuv420p"),
            True,
        ),
        (
            "vaapi",
            "/dev/dri/renderD128",
            "h264_vaapi",
            "tt",
            90,
            "deinterlace_vaapi",
            "vaapi",
            (
                "bwdif=mode=send_frame:parity=tff:deint=all,"
                "setfield=prog,transpose=clock,format=nv12,hwupload"
            ),
            True,
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            "h264_qsv",
            "tt",
            90,
            "vpp_qsv",
            None,
            (
                "bwdif=mode=send_frame:parity=tff:deint=all,"
                "setfield=prog,transpose=clock,format=nv12"
            ),
            False,
        ),
        (
            "videotoolbox",
            None,
            "h264_videotoolbox",
            "progressive",
            90,
            "transpose_vt",
            "videotoolbox",
            "transpose=clock,format=nv12",
            True,
        ),
    ],
)
def test_transform_fallback_builds_system_memory_command(
    monkeypatch,
    tmp_path,
    fallback,
    hwaccel,
    device,
    encoder,
    field_order,
    rotation,
    hardware_filter,
    expected_hwaccel,
    expected_filter,
    expected_decode,
):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel(hwaccel)
    monkeypatch.setattr(base_module, "require_hardware_encoder", AsyncMock())
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        AsyncMock(return_value=True),
    )
    probe_transform = AsyncMock(return_value=False)
    monkeypatch.setattr(base_module, "probe_hardware_transform", probe_transform)
    monkeypatch.setattr(
        strategy,
        "resolve_device",
        AsyncMock(return_value=device),
    )
    capabilities = _capabilities()
    if fallback == "missing_filter":
        capabilities = _capabilities(
            filters=capabilities.filters - {hardware_filter},
        )
    context = _eligible_hardware_context(hwaccel, capabilities=capabilities)
    context.metadata = replace(
        context.metadata,
        width=1920,
        rotation=rotation,
        field_order=field_order,
    )

    context.hardware = asyncio.run(strategy.prepare_hardware(context, str(media)))
    cmd = asyncio.run(transcoder._build_hls_cmd(str(media), tmp_path, context))

    assert context.hardware == base_module.HardwareRuntime(
        device,
        expected_decode,
        False,
    )
    assert cmd[cmd.index("-c:v") + 1] == encoder
    assert cmd[cmd.index("-vf") + 1] == expected_filter
    assert "-hwaccel_output_format" not in cmd
    if expected_hwaccel is None:
        assert "-hwaccel" not in cmd
    else:
        assert cmd[cmd.index("-hwaccel") + 1] == expected_hwaccel
    if fallback == "missing_filter":
        probe_transform.assert_not_awaited()
    else:
        probe_transform.assert_awaited_once()


@pytest.mark.parametrize(
    ("hwaccel", "device", "encoder", "expected_filter"),
    [
        ("nvenc", "0", "h264_nvenc", "format=yuv420p"),
        ("qsv", "/dev/dri/renderD128", "h264_qsv", "format=nv12"),
        (
            "vaapi",
            "/dev/dri/renderD128",
            "h264_vaapi",
            "format=nv12,hwupload",
        ),
        ("videotoolbox", None, "h264_videotoolbox", "format=nv12"),
    ],
)
def test_runtime_decode_failure_builds_software_decode_command(
    monkeypatch,
    tmp_path,
    hwaccel,
    device,
    encoder,
    expected_filter,
):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    strategy = get_hwaccel(hwaccel)
    require_encoder = AsyncMock()
    monkeypatch.setattr(
        base_module,
        "require_hardware_encoder",
        require_encoder,
        raising=False,
    )
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        AsyncMock(return_value=False),
        raising=False,
    )
    monkeypatch.setattr(
        strategy,
        "resolve_device",
        AsyncMock(return_value=device),
    )
    context = _eligible_hardware_context(hwaccel)

    context.hardware = asyncio.run(strategy.prepare_hardware(context, str(media)))
    cmd = asyncio.run(transcoder._build_hls_cmd(str(media), tmp_path, context))

    assert context.hardware == base_module.HardwareRuntime(device, False)
    assert "-hwaccel" not in cmd
    assert cmd[cmd.index("-c:v") + 1] == encoder
    assert cmd[cmd.index("-vf") + 1] == expected_filter
    require_encoder.assert_awaited_once()


def test_vaapi_prepare_hardware_uses_device_once(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    device = "/dev/dri/renderD128"
    resolve = AsyncMock(return_value=device)
    require_encoder = AsyncMock()
    probe_decode = AsyncMock(return_value=True)
    monkeypatch.setattr(vaapi_module, "resolve_vaapi_device", resolve)
    monkeypatch.setattr(
        base_module,
        "require_hardware_encoder",
        require_encoder,
        raising=False,
    )
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        probe_decode,
        raising=False,
    )

    runtime = asyncio.run(
        get_hwaccel("vaapi").prepare_hardware(
            _eligible_hardware_context("vaapi"),
            str(media),
        )
    )

    assert runtime == base_module.HardwareRuntime(device, True)
    resolve.assert_awaited_once()
    encoder_args = require_encoder.await_args.args[-1]
    assert encoder_args[:2] == ["-vaapi_device", device]
    assert encoder_args[encoder_args.index("-vf") + 1] == "format=nv12,hwupload"
    assert "h264_vaapi" in encoder_args
    decode_args = probe_decode.await_args.args[-1]
    assert decode_args[decode_args.index("-vaapi_device") + 1] == device
    assert "vaapi" in decode_args


def test_qsv_prepare_hardware_uses_device_once(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    device = "/dev/dri/renderD128"
    resolve = AsyncMock(return_value=device)
    require_encoder = AsyncMock()
    probe_decode = AsyncMock(return_value=True)
    monkeypatch.setattr(qsv_module, "resolve_vaapi_device", resolve)
    monkeypatch.setattr(
        base_module,
        "require_hardware_encoder",
        require_encoder,
        raising=False,
    )
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        probe_decode,
        raising=False,
    )

    runtime = asyncio.run(
        get_hwaccel("qsv").prepare_hardware(
            _eligible_hardware_context("qsv"),
            str(media),
        )
    )

    assert runtime == base_module.HardwareRuntime(device, True)
    resolve.assert_awaited_once()
    encoder_args = require_encoder.await_args.args[-1]
    device_arg = f"qsv=qs:hw,child_device={device},child_device_type=vaapi"
    assert encoder_args[encoder_args.index("-init_hw_device") + 1] == device_arg
    assert encoder_args[encoder_args.index("-vf") + 1] == "format=nv12,hwupload"
    assert "h264_qsv" in encoder_args
    decode_args = probe_decode.await_args.args[-1]
    assert decode_args[decode_args.index("-init_hw_device") + 1] == device_arg
    assert "qsv" in decode_args


@pytest.mark.parametrize(
    "case",
    ["unsupported_codec", "missing_hwaccel", "qsv_hdr"],
)
def test_prepare_hardware_skips_ineligible_decode(monkeypatch, tmp_path, case):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    require_encoder = AsyncMock()
    probe_decode = AsyncMock(return_value=True)
    monkeypatch.setattr(
        base_module,
        "require_hardware_encoder",
        require_encoder,
        raising=False,
    )
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        probe_decode,
        raising=False,
    )
    monkeypatch.setattr(
        qsv_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )
    if case == "unsupported_codec":
        context = TranscodeContext(
            options=TranscodeOptions(hwaccel="nvenc"),
            metadata=MediaProbe(
                video_stream_index=0,
                codec="vp9",
                profile="Profile 0",
                bit_depth=8,
                pixel_format="yuv420p",
            ),
            capabilities=_capabilities(),
        )
    elif case == "missing_hwaccel":
        context = _eligible_hardware_context(
            "nvenc",
            capabilities=_capabilities(hwaccels=()),
        )
    else:
        context = TranscodeContext(
            options=TranscodeOptions(hwaccel="qsv"),
            metadata=MediaProbe(
                video_stream_index=0,
                codec="hevc",
                profile="Main 10",
                bit_depth=10,
                pixel_format="yuv420p10le",
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt2020nc",
            ),
            capabilities=_capabilities(),
        )

    runtime = asyncio.run(
        get_hwaccel(context.options.hwaccel).prepare_hardware(
            context,
            str(media),
        )
    )

    assert runtime is not None
    assert runtime.can_decode is False
    require_encoder.assert_awaited_once()
    probe_decode.assert_not_awaited()


def test_prepare_hardware_rejects_missing_encoder_before_device(monkeypatch):
    context = _eligible_hardware_context(
        "vaapi",
        capabilities=_capabilities(encoders=("aac",)),
    )
    resolve = AsyncMock(side_effect=AssertionError("device resolved"))
    monkeypatch.setattr(vaapi_module, "resolve_vaapi_device", resolve)

    with pytest.raises(RuntimeError, match="encoders: h264_vaapi"):
        asyncio.run(get_hwaccel("vaapi").prepare_hardware(context, "input.mkv"))

    resolve.assert_not_awaited()


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (MediaProbe(bit_depth=8, color_transfer="bt709"), HDRType.SDR),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="SMPTE2084",
                color_primaries="BT2020",
                color_space="BT2020NC",
            ),
            HDRType.HDR10,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="arib-std-b67",
                color_primaries="bt2020",
                color_space="bt2020_ncl",
            ),
            HDRType.HLG,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt2020nc",
                hdr10_plus=True,
            ),
            HDRType.HDR10_PLUS,
        ),
        (
            MediaProbe(
                dovi_profile=8,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=1,
            ),
            HDRType.DOVI_COMPATIBLE,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt2020nc",
                dovi_profile=7,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=6,
            ),
            HDRType.DOVI_COMPATIBLE,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt2020nc",
                dovi_profile=8,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=6,
            ),
            HDRType.DOVI_COMPATIBLE,
        ),
        (
            MediaProbe(
                bit_depth=8,
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt2020nc",
                dovi_profile=7,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=6,
            ),
            HDRType.DOVI_ONLY,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="bt709",
                color_primaries="bt2020",
                color_space="bt2020nc",
                dovi_profile=7,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=6,
            ),
            HDRType.DOVI_ONLY,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="smpte2084",
                color_primaries="bt709",
                color_space="bt2020nc",
                dovi_profile=7,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=6,
            ),
            HDRType.DOVI_ONLY,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt709",
                dovi_profile=7,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=6,
            ),
            HDRType.DOVI_ONLY,
        ),
        (
            MediaProbe(
                bit_depth=10,
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt2020nc",
                dovi_profile=7,
                dovi_bl_present=False,
                dovi_bl_signal_compatibility_id=6,
            ),
            HDRType.DOVI_ONLY,
        ),
        (
            MediaProbe(
                bit_depth=10,
                dovi_profile=4,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=2,
            ),
            HDRType.DOVI_ONLY,
        ),
        (
            MediaProbe(
                dovi_profile=5,
                dovi_bl_present=True,
                dovi_bl_signal_compatibility_id=0,
            ),
            HDRType.DOVI_ONLY,
        ),
        (
            MediaProbe(
                bit_depth=8,
                color_transfer="smpte2084",
                color_primaries="bt2020",
                color_space="bt2020nc",
            ),
            HDRType.UNKNOWN,
        ),
        (
            MediaProbe(bit_depth=10, color_transfer="smpte2084"),
            HDRType.UNKNOWN,
        ),
    ],
)
def test_classify_hdr(metadata, expected):
    assert classify_hdr(metadata) is expected


@pytest.mark.parametrize("profile", [7, 8])
def test_supported_hdr_guard_accepts_id6_hdr10_base(profile):
    metadata = MediaProbe(
        bit_depth=10,
        color_transfer="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
        dovi_profile=profile,
        dovi_bl_present=True,
        dovi_bl_signal_compatibility_id=6,
    )

    transcoder._require_supported_hdr(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        MediaProbe(
            bit_depth=10,
            dovi_profile=4,
            dovi_bl_present=True,
            dovi_bl_signal_compatibility_id=2,
        ),
        MediaProbe(
            bit_depth=10,
            dovi_profile=5,
            dovi_bl_present=True,
            dovi_bl_signal_compatibility_id=0,
        ),
    ],
)
def test_supported_hdr_guard_rejects_dovi_without_hdr10_base(metadata):
    with pytest.raises(RuntimeError, match="Dolby Vision-only"):
        transcoder._require_supported_hdr(metadata)


@pytest.mark.parametrize(
    ("hdr_type", "is_hdr10", "is_hlg", "needs_tonemap"),
    [
        (HDRType.SDR, False, False, False),
        (HDRType.HDR10, True, False, True),
        (HDRType.HLG, False, True, True),
        (HDRType.HDR10_PLUS, True, False, True),
        (HDRType.DOVI_COMPATIBLE, True, False, True),
        (HDRType.DOVI_ONLY, False, False, False),
        (HDRType.UNKNOWN, False, False, False),
    ],
)
def test_context_detects_hdr(hdr_type, is_hdr10, is_hlg, needs_tonemap):
    metadata_by_type = {
        HDRType.SDR: MediaProbe(bit_depth=8, color_transfer="bt709"),
        HDRType.HDR10: MediaProbe(
            bit_depth=10,
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        HDRType.HLG: MediaProbe(
            bit_depth=10,
            color_transfer="arib-std-b67",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        HDRType.HDR10_PLUS: MediaProbe(
            bit_depth=10,
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_space="bt2020nc",
            hdr10_plus=True,
        ),
        HDRType.DOVI_COMPATIBLE: MediaProbe(
            dovi_profile=8,
            dovi_bl_present=True,
            dovi_bl_signal_compatibility_id=1,
        ),
        HDRType.DOVI_ONLY: MediaProbe(dovi_profile=5),
        HDRType.UNKNOWN: MediaProbe(bit_depth=8, color_transfer="smpte2084"),
    }
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=metadata_by_type[hdr_type],
    )

    assert context.hdr_type is hdr_type
    assert context.is_hdr10 is is_hdr10
    assert context.is_hlg is is_hlg
    assert context.needs_tonemap is needs_tonemap


@pytest.mark.parametrize(
    (
        "width",
        "height",
        "sar",
        "rotation",
        "resolution",
        "display",
        "target",
        "needs_downscale",
        "needs_square_pixels",
    ),
    [
        (1920, 1080, None, 0, "720p", (1920, 1080), (1280, 720), True, False),
        (1920, 1080, None, 90, "1080p", (1080, 1920), (592, 1080), True, False),
        (
            720,
            576,
            (16, 15),
            0,
            "original",
            (768, 576),
            (768, 576),
            False,
            True,
        ),
        (
            720,
            576,
            (16, 15),
            90,
            "original",
            (576, 768),
            (576, 768),
            False,
            True,
        ),
        (
            720,
            576,
            (64, 45),
            0,
            "original",
            (1024, 576),
            (1024, 576),
            False,
            True,
        ),
        (1920, 1080, (1, 1), 0, "original", (1920, 1080), None, False, False),
    ],
)
def test_context_uses_display_geometry(
    width,
    height,
    sar,
    rotation,
    resolution,
    display,
    target,
    needs_downscale,
    needs_square_pixels,
):
    context = TranscodeContext(
        options=TranscodeOptions(resolution=resolution),
        metadata=MediaProbe(
            width=width,
            height=height,
            sample_aspect_ratio=sar,
            rotation=rotation,
        ),
    )

    assert (context.display_width, context.display_height) == tuple(
        Fraction(value) for value in display
    )
    assert context.needs_downscale is needs_downscale
    assert context.needs_square_pixels is needs_square_pixels
    assert context.needs_scale is (target is not None)
    if target is None:
        assert context.scale_width is None
        assert context.scale_height is None
    else:
        assert (context.scale_width, context.scale_height) == tuple(
            str(value) for value in target
        )


@pytest.mark.parametrize(
    ("field_order", "rotation", "sar", "expected"),
    [
        (
            "tt",
            90,
            (16, 15),
            [
                "bwdif=mode=send_frame:parity=tff:deint=all",
                "setfield=prog",
                "transpose=clock",
                "scale=576:768",
                "setsar=1",
            ],
        ),
        (
            "bb",
            180,
            None,
            [
                "bwdif=mode=send_frame:parity=bff:deint=all",
                "setfield=prog",
                "transpose=clock",
                "transpose=clock",
            ],
        ),
        ("progressive", 270, None, ["transpose=cclock"]),
        ("unknown", 0, None, []),
    ],
)
def test_cpu_geometry_filters(field_order, rotation, sar, expected):
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(
            width=720,
            height=576,
            sample_aspect_ratio=sar,
            rotation=rotation,
            field_order=field_order,
        ),
    )

    assert base_module.cpu_geometry_filters(context) == expected


def _capabilities(
    *,
    encoders=(
        "aac",
        "h264_nvenc",
        "h264_qsv",
        "h264_vaapi",
        "h264_videotoolbox",
        "libx264",
    ),
    hwaccels=("cuda", "qsv", "vaapi", "videotoolbox"),
    filters=(
        "bwdif",
        "deinterlace_vaapi",
        "format",
        "hwupload",
        "hwupload_cuda",
        "scale",
        "scale_cuda",
        "scale_vaapi",
        "scale_vt",
        "setfield",
        "setsar",
        "tonemap",
        "tonemap_vaapi",
        "transpose",
        "transpose_vaapi",
        "transpose_vt",
        "vpp_qsv",
        "yadif_cuda",
        "zscale",
    ),
    encoder_options=(
        "crf",
        "forced-idr",
        "forced_idr",
        "mbbrc",
        "preset",
        "prio_speed",
        "profile",
        "qp",
        "rc_init_occupancy",
        "rc_mode",
    ),
    bsfs=("h264_metadata",),
    muxers=("hls", "mpegts"),
):
    return FFmpegCapabilities(
        executable="ffmpeg",
        encoders=frozenset(encoders),
        filters=frozenset(filters),
        hwaccels=frozenset(hwaccels),
        bsfs=frozenset(bsfs),
        muxers=frozenset(muxers),
        encoder_options=frozenset(encoder_options),
    )


def _hdr_context(
    hwaccel=None,
    resolution="original",
    transfer="smpte2084",
    capabilities=None,
):
    return TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel, resolution=resolution),
        metadata=MediaProbe(
            video_stream_index=0,
            audio_stream_index=1,
            avg_frame_rate=Fraction(24),
            r_frame_rate=Fraction(24),
            pixel_format="yuv420p10le",
            bit_depth=10,
            width=3840,
            height=2160,
            color_transfer=transfer,
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        capabilities=capabilities,
    )


def _qsv_hdr_metadata(hdr_type: HDRType) -> MediaProbe:
    metadata = {
        HDRType.HDR10: MediaProbe(
            bit_depth=10,
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        HDRType.HLG: MediaProbe(
            bit_depth=10,
            color_transfer="arib-std-b67",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        HDRType.HDR10_PLUS: MediaProbe(
            bit_depth=10,
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_space="bt2020nc",
            hdr10_plus=True,
        ),
        HDRType.DOVI_COMPATIBLE: MediaProbe(
            dovi_profile=8,
            dovi_bl_present=True,
            dovi_bl_signal_compatibility_id=1,
        ),
    }
    return metadata[hdr_type]


@pytest.mark.parametrize("transfer", ["smpte2084", "arib-std-b67"])
def test_software_hdr_filters(transfer):
    context = _hdr_context(transfer=transfer)

    assert get_hwaccel(None).video_filters(context) == [
        "zscale=transfer=linear:npl=100",
        "format=gbrpf32le",
        "tonemap=hable:desat=0",
        "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv",
        "format=yuv420p",
    ]


def test_software_scaled_hdr_filters():
    context = _hdr_context(resolution="720p")

    assert get_hwaccel(None).video_filters(context)[0] == (
        "zscale=transfer=linear:npl=100:"
        f"w='{context.scale_width}':h='{context.scale_height}'"
    )


def test_nvenc_hdr_uses_software_tonemap():
    context = _hdr_context(hwaccel="nvenc")
    strategy = get_hwaccel("nvenc")

    assert asyncio.run(strategy.input_args(context)) == ["-hwaccel", "cuda"]
    assert strategy.video_filters(context) == [
        "zscale=transfer=linear:npl=100",
        "format=gbrpf32le",
        "tonemap=hable:desat=0",
        "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv",
        "format=yuv420p",
        "hwupload_cuda",
    ]


def test_hdr_cmd_sets_bt709_bitstream_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    context = _hdr_context(resolution="720p")

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    vf = cmd[cmd.index("-vf") + 1]
    assert not vf.startswith("scale='")
    assert "tonemap=hable:desat=0" in vf
    assert "-color_primaries" not in cmd
    assert "-color_trc" not in cmd
    assert "-colorspace" not in cmd
    assert "-color_range" not in cmd
    assert cmd[cmd.index("-bsf:v") + 1] == (
        "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
        "matrix_coefficients=1:video_full_range_flag=0"
    )


def test_hdr_cmd_omits_unavailable_optional_bitstream_filter(tmp_path):
    context = _hdr_context(capabilities=_capabilities(bsfs=()))

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    assert "-bsf:v" not in cmd


@pytest.mark.parametrize(
    ("capabilities", "missing"),
    [
        (
            _capabilities(
                encoders=(
                    "aac",
                    "h264_nvenc",
                    "h264_qsv",
                    "h264_vaapi",
                    "h264_videotoolbox",
                )
            ),
            "encoders: libx264",
        ),
        (_capabilities(encoders=("libx264",)), "encoders: aac"),
        (_capabilities(muxers=("mpegts",)), "muxers: hls"),
        (_capabilities(muxers=("hls",)), "muxers: mpegts"),
        (
            _capabilities(filters=("format", "tonemap")),
            "filters: zscale",
        ),
    ],
)
def test_build_rejects_missing_required_capabilities(tmp_path, capabilities, missing):
    context = _hdr_context(capabilities=capabilities)

    with pytest.raises(RuntimeError, match=missing):
        asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))


def test_software_cmd(monkeypatch, tmp_path):
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(video_stream_index=0, audio_stream_index=1),
    )

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-crf") + 1] == "23"
    assert cmd[cmd.index("-hls_time") + 1] == "6"
    assert "-level" not in cmd
    assert cmd[cmd.index("-hls_flags") + 1] == "independent_segments"
    keyframe_index = cmd.index("-force_key_frames:0")
    assert cmd[keyframe_index : keyframe_index + 8] == [
        "-force_key_frames:0",
        "expr:if(isnan(prev_forced_t),1,gte(t,prev_forced_t+6))",
        "-flags:v:0",
        "+cgop",
        "-sc_threshold:v:0",
        "0",
        "-c:a",
        "aac",
    ]
    assert "-vf" not in cmd


@pytest.mark.parametrize(
    ("hwaccel", "device"),
    [
        (None, None),
        ("nvenc", "0"),
        ("qsv", "/dev/dri/renderD128"),
        ("vaapi", "/dev/dri/renderD128"),
        ("videotoolbox", None),
    ],
)
def test_final_command_disables_autorotate_and_clears_rotation(
    monkeypatch, tmp_path, hwaccel, device
):
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel),
        metadata=MediaProbe(
            video_stream_index=0,
            width=1920,
            height=1080,
            rotation=90,
            pixel_format="yuv420p",
        ),
        capabilities=_capabilities(),
        hardware=(
            base_module.HardwareRuntime(device, False, False)
            if hwaccel is not None
            else None
        ),
    )

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    display_rotation_index = cmd.index("-display_rotation")
    assert cmd[display_rotation_index + 1] == "0"
    assert display_rotation_index < cmd.index("-i")
    assert cmd.index("-noautorotate") < cmd.index("-i")
    metadata_index = cmd.index("-metadata:s:v:0")
    assert cmd[metadata_index + 1] == "rotate=0"
    assert cmd[cmd.index("-c:v") + 1] == context.options.encoder


def test_interlaced_command_marks_output_progressive(monkeypatch, tmp_path):
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(
            video_stream_index=0,
            width=1920,
            height=1080,
            field_order="tb",
        ),
        capabilities=_capabilities(),
    )

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    field_order_index = cmd.index("-field_order")
    assert cmd[field_order_index + 1] == "progressive"


def test_interlaced_command_requires_bwdif_only_when_used(monkeypatch, tmp_path):
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    capabilities = _capabilities(filters=_capabilities().filters - {"bwdif"})

    progressive = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(
            video_stream_index=0,
            width=1920,
            height=1080,
            field_order="progressive",
        ),
        capabilities=capabilities,
    )
    asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, progressive))

    interlaced = replace(
        progressive,
        metadata=replace(progressive.metadata, field_order="tt"),
    )
    with pytest.raises(RuntimeError, match="filters: bwdif"):
        asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, interlaced))


def test_build_maps_selected_stream_indexes(tmp_path):
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(video_stream_index=3, audio_stream_index=1),
        capabilities=_capabilities(),
    )

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    maps = [cmd[index + 1] for index, value in enumerate(cmd) if value == "-map"]
    assert maps == ["0:3", "0:1"]
    assert "-an" not in cmd


def test_build_silent_video_omits_audio_encoder_and_capability(tmp_path):
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(video_stream_index=2),
        capabilities=_capabilities(encoders=("libx264",)),
    )

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    maps = [cmd[index + 1] for index, value in enumerate(cmd) if value == "-map"]
    assert maps == ["0:2"]
    assert "-an" in cmd
    assert "-c:a" not in cmd


def test_build_requires_aac_when_audio_is_selected(tmp_path):
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(video_stream_index=0, audio_stream_index=1),
        capabilities=_capabilities(encoders=("libx264",)),
    )

    with pytest.raises(RuntimeError, match="encoders: aac"):
        asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))


def test_build_rejects_input_without_video_stream(tmp_path):
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(audio_stream_index=1),
        capabilities=_capabilities(),
    )

    with pytest.raises(RuntimeError, match="no transcodable video stream"):
        asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))


def test_nvenc_args():
    strategy = get_hwaccel("nvenc")
    options = TranscodeOptions(hwaccel="nvenc", quality="high")
    context = TranscodeContext(
        options=options,
        metadata=MediaProbe(
            avg_frame_rate=Fraction(47, 2),
            r_frame_rate=Fraction(47, 2),
            pixel_format="yuv420p",
        ),
    )

    assert asyncio.run(strategy.input_args(context)) == [
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
    ]
    scaled_context = TranscodeContext(
        options=TranscodeOptions(hwaccel="nvenc", resolution="720p"),
        metadata=MediaProbe(width=1920, height=1080),
    )
    assert asyncio.run(strategy.input_args(scaled_context)) == ["-hwaccel", "cuda"]
    assert strategy.video_filters(context) == ["scale_cuda=format=yuv420p"]
    ten_bit_context = TranscodeContext(
        options=options,
        metadata=MediaProbe(pixel_format="yuv420p10le"),
    )
    assert strategy.video_filters(ten_bit_context) == ["scale_cuda=format=yuv420p"]
    scaled_ten_bit_context = TranscodeContext(
        options=scaled_context.options,
        metadata=MediaProbe(
            width=1920,
            height=1080,
            pixel_format="yuv420p10le",
        ),
    )
    assert strategy.video_filters(scaled_ten_bit_context) == [
        "scale=1280:720",
        "setsar=1",
        "format=yuv420p",
    ]
    assert strategy.encoder_args(context) == [
        "-preset",
        "p7",
        "-b:v",
        "6000k",
        "-maxrate",
        "6000k",
        "-bufsize",
        "12000k",
        "-forced-idr",
        "1",
    ]
    assert strategy.keyframe_args(context) == [
        "-force_key_frames:0",
        "expr:if(isnan(prev_forced_t),1,gte(t,prev_forced_t+6))",
        "-flags:v:0",
        "+cgop",
        "-g:v:0",
        "141",
        "-keyint_min:v:0",
        "141",
    ]


@pytest.mark.parametrize(
    ("hwaccel", "device", "rotation", "field_order", "expected"),
    [
        (
            "nvenc",
            "0",
            0,
            "tt",
            [
                "yadif_cuda=mode=send_frame:parity=tff:deint=all",
                "setfield=prog",
                "scale_cuda=format=yuv420p",
            ],
        ),
        (
            "vaapi",
            "/dev/dri/renderD128",
            90,
            "tt",
            [
                "deinterlace_vaapi=rate=frame:auto=0",
                "setfield=prog",
                "transpose_vaapi=dir=clock",
                "scale_vaapi=format=nv12",
            ],
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            270,
            "bb",
            [
                "vpp_qsv=deinterlace=advanced:rate=frame:transpose=cclock:format=nv12",
                "setfield=prog",
            ],
        ),
        (
            "videotoolbox",
            None,
            270,
            "progressive",
            ["transpose_vt=dir=cclock"],
        ),
    ],
)
def test_hardware_geometry_filters(hwaccel, device, rotation, field_order, expected):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel),
        metadata=MediaProbe(
            width=1920,
            height=1080,
            rotation=rotation,
            field_order=field_order,
            pixel_format="yuv420p",
        ),
        capabilities=_capabilities(),
        hardware=base_module.HardwareRuntime(device, True, True),
    )

    assert get_hwaccel(hwaccel).video_filters(context) == expected


@pytest.mark.parametrize(
    ("hwaccel", "device", "expected"),
    [
        (
            "nvenc",
            "0",
            [
                "scale_cuda=w=1280:h=720:format=yuv420p",
                "setsar=1",
            ],
        ),
        (
            "vaapi",
            "/dev/dri/renderD128",
            [
                "scale_vaapi=w=1280:h=720:format=nv12",
                "setsar=1",
            ],
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            [
                "vpp_qsv=w=1280:h=720:format=nv12",
                "setsar=1",
            ],
        ),
        (
            "videotoolbox",
            None,
            [
                "scale_vt=w=1280:h=720",
                "setsar=1",
            ],
        ),
    ],
)
def test_hardware_scale_filters(hwaccel, device, expected):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel, resolution="720p"),
        metadata=MediaProbe(
            width=1920,
            height=1080,
            field_order="progressive",
            pixel_format="yuv420p",
        ),
        capabilities=_capabilities(),
        hardware=base_module.HardwareRuntime(device, True, True),
    )

    assert get_hwaccel(hwaccel).video_filters(context) == expected


@pytest.mark.parametrize(
    ("hwaccel", "device", "rotation", "field_order", "expected"),
    [
        (
            "nvenc",
            "0",
            0,
            "tt",
            [
                "yadif_cuda=mode=send_frame:parity=tff:deint=all",
                "setfield=prog",
                "scale_cuda=w=1280:h=720:format=yuv420p",
                "setsar=1",
            ],
        ),
        (
            "vaapi",
            "/dev/dri/renderD128",
            90,
            "tt",
            [
                "deinterlace_vaapi=rate=frame:auto=0",
                "setfield=prog",
                "transpose_vaapi=dir=clock",
                "scale_vaapi=w=592:h=1080:format=nv12",
                "setsar=1",
            ],
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            270,
            "bb",
            [
                (
                    "vpp_qsv=deinterlace=advanced:rate=frame:transpose=cclock:"
                    "w=592:h=1080:format=nv12"
                ),
                "setfield=prog",
                "setsar=1",
            ],
        ),
        (
            "videotoolbox",
            None,
            90,
            "progressive",
            [
                "transpose_vt=dir=clock",
                "scale_vt=w=592:h=1080",
                "setsar=1",
            ],
        ),
    ],
)
def test_scaled_hardware_geometry_filters(
    hwaccel,
    device,
    rotation,
    field_order,
    expected,
):
    context = TranscodeContext(
        options=TranscodeOptions(
            hwaccel=hwaccel,
            resolution="720p" if hwaccel == "nvenc" else "1080p",
        ),
        metadata=MediaProbe(
            width=1920,
            height=1080,
            rotation=rotation,
            field_order=field_order,
            pixel_format="yuv420p",
        ),
        capabilities=_capabilities(),
        hardware=base_module.HardwareRuntime(device, True, True),
    )

    assert get_hwaccel(hwaccel).video_filters(context) == expected


@pytest.mark.parametrize(
    ("hwaccel", "device", "expected_scale"),
    [
        ("nvenc", "0", "scale_cuda=w=768:h=576:format=yuv420p"),
        (
            "vaapi",
            "/dev/dri/renderD128",
            "scale_vaapi=w=768:h=576:format=nv12",
        ),
        (
            "qsv",
            "/dev/dri/renderD128",
            "vpp_qsv=w=768:h=576:format=nv12",
        ),
        ("videotoolbox", None, "scale_vt=w=768:h=576"),
    ],
)
def test_hardware_scale_normalizes_sar(hwaccel, device, expected_scale):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel),
        metadata=MediaProbe(
            width=720,
            height=576,
            sample_aspect_ratio=(16, 15),
            field_order="progressive",
            pixel_format="yuv420p",
        ),
        capabilities=_capabilities(),
        hardware=base_module.HardwareRuntime(device, True, True),
    )

    assert get_hwaccel(hwaccel).video_filters(context) == [
        expected_scale,
        "setsar=1",
    ]


@pytest.mark.parametrize(
    ("hwaccel", "device", "expected_tail"),
    [
        ("nvenc", "0", ["format=yuv420p"]),
        ("vaapi", "/dev/dri/renderD128", ["format=nv12", "hwupload"]),
        ("qsv", "/dev/dri/renderD128", ["format=nv12"]),
        ("videotoolbox", None, ["format=nv12"]),
    ],
)
def test_hardware_geometry_runtime_failure_uses_cpu(hwaccel, device, expected_tail):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel),
        metadata=MediaProbe(
            width=1920,
            height=1080,
            rotation=90,
            field_order="tt",
            pixel_format="yuv420p",
        ),
        capabilities=_capabilities(),
        hardware=base_module.HardwareRuntime(device, True, False),
    )

    filters = get_hwaccel(hwaccel).video_filters(context)

    assert filters[:3] == [
        "bwdif=mode=send_frame:parity=tff:deint=all",
        "setfield=prog",
        "transpose=clock",
    ]
    assert filters[3:] == expected_tail


def test_nvenc_falls_back_to_software_decoding():
    strategy = get_hwaccel("nvenc")
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="nvenc"),
        metadata=MediaProbe(pixel_format="yuv420p10le"),
        capabilities=_capabilities(hwaccels=()),
    )

    assert asyncio.run(strategy.input_args(context)) == []
    assert strategy.video_filters(context) == ["format=yuv420p"]


def test_nvenc_fallback_omits_unavailable_cuda_upload():
    context = _hdr_context(
        hwaccel="nvenc",
        capabilities=_capabilities(
            hwaccels=(), filters=("format", "tonemap", "zscale")
        ),
    )

    filters = get_hwaccel("nvenc").video_filters(context)

    assert "hwupload_cuda" not in filters
    assert filters[-1] == "format=yuv420p"


def test_qsv_args(monkeypatch):
    device = "/dev/dri/renderD128"
    strategy = get_hwaccel("qsv")
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv"),
        metadata=MediaProbe(
            avg_frame_rate=Fraction(25),
            r_frame_rate=Fraction(25),
        ),
    )
    scaled_context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv", resolution="720p"),
        metadata=MediaProbe(width=1920, height=1080),
    )

    monkeypatch.setattr(
        qsv_module, "resolve_vaapi_device", AsyncMock(return_value=device)
    )

    assert asyncio.run(strategy.input_args(context)) == [
        "-init_hw_device",
        f"qsv=qs:hw,child_device={device},child_device_type=vaapi",
        "-filter_hw_device",
        "qs",
        "-hwaccel",
        "qsv",
        "-hwaccel_device",
        "qs",
        "-hwaccel_output_format",
        "qsv",
    ]
    assert asyncio.run(strategy.input_args(scaled_context)) == [
        "-init_hw_device",
        f"qsv=qs:hw,child_device={device},child_device_type=vaapi",
        "-filter_hw_device",
        "qs",
        "-hwaccel",
        "qsv",
        "-hwaccel_device",
        "qs",
        "-hwaccel_output_format",
        "qsv",
    ]
    assert strategy.video_filters(context) == ["vpp_qsv=format=nv12"]
    assert strategy.video_filters(scaled_context) == [
        "scale=1280:720",
        "setsar=1",
        "format=nv12",
    ]
    assert strategy.encoder_args(context) == [
        "-preset",
        "veryfast",
        "-b:v",
        "3000k",
        "-maxrate",
        "3001k",
        "-bufsize",
        "12000k",
        "-mbbrc",
        "1",
        "-rc_init_occupancy",
        "6000000",
        "-forced_idr",
        "1",
    ]
    assert strategy.keyframe_args(context) == [
        "-force_key_frames:0",
        "expr:if(isnan(prev_forced_t),1,gte(t,prev_forced_t+6))",
        "-flags:v:0",
        "+cgop",
        "-g:v:0",
        "150",
        "-keyint_min:v:0",
        "150",
    ]


@pytest.mark.parametrize(
    "hdr_type",
    [
        HDRType.HDR10,
        HDRType.HLG,
        HDRType.HDR10_PLUS,
        HDRType.DOVI_COMPATIBLE,
    ],
)
def test_qsv_hdr_disables_hardware_decode(monkeypatch, hdr_type):
    device = "/dev/dri/renderD128"
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv"),
        metadata=_qsv_hdr_metadata(hdr_type),
        capabilities=_capabilities(),
    )
    monkeypatch.setattr(
        qsv_module,
        "resolve_vaapi_device",
        AsyncMock(return_value=device),
    )

    assert asyncio.run(get_hwaccel("qsv").input_args(context)) == [
        "-init_hw_device",
        f"qsv=qs:hw,child_device={device},child_device_type=vaapi",
        "-filter_hw_device",
        "qs",
    ]


def test_qsv_falls_back_to_software_decoding(monkeypatch):
    device = "/dev/dri/renderD128"
    strategy = get_hwaccel("qsv")
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv"),
        capabilities=_capabilities(hwaccels=()),
    )
    monkeypatch.setattr(
        qsv_module, "resolve_vaapi_device", AsyncMock(return_value=device)
    )

    args = asyncio.run(strategy.input_args(context))

    assert "-hwaccel" not in args
    assert strategy.video_filters(context) == ["format=nv12"]


@pytest.mark.parametrize(
    "hdr_type",
    [
        HDRType.HDR10,
        HDRType.HLG,
        HDRType.HDR10_PLUS,
        HDRType.DOVI_COMPATIBLE,
    ],
)
def test_qsv_hdr_filters_use_software_tonemap(hdr_type):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv"),
        metadata=_qsv_hdr_metadata(hdr_type),
        capabilities=_capabilities(),
    )

    assert get_hwaccel("qsv").video_filters(context) == [
        "zscale=transfer=linear:npl=100",
        "format=gbrpf32le",
        "tonemap=hable:desat=0",
        "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv",
        "format=nv12",
        "hwupload",
    ]


def test_qsv_scaled_hdr_keeps_scaling_in_zscale():
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv", resolution="720p"),
        metadata=MediaProbe(
            width=3840,
            height=2160,
            bit_depth=10,
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        capabilities=_capabilities(),
    )

    filters = get_hwaccel("qsv").video_filters(context)

    assert filters[0] == (
        "zscale=transfer=linear:npl=100:"
        f"w='{context.scale_width}':h='{context.scale_height}'"
    )
    assert filters[-3:] == ["format=nv12", "setsar=1", "hwupload"]
    assert all(not value.startswith("vpp_qsv") for value in filters)


def test_qsv_hdr_command_does_not_require_vpp_qsv(monkeypatch, tmp_path):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv"),
        metadata=MediaProbe(
            video_stream_index=0,
            bit_depth=10,
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        capabilities=_capabilities(filters=("format", "hwupload", "tonemap", "zscale")),
    )
    monkeypatch.setattr(
        qsv_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))
    vf = cmd[cmd.index("-vf") + 1]

    assert "vpp_qsv" not in vf
    assert vf.endswith("format=nv12,hwupload")


@pytest.mark.parametrize("missing", ["format", "hwupload", "tonemap", "zscale"])
def test_qsv_hdr_command_requires_cpu_tonemap_filters(monkeypatch, tmp_path, missing):
    filters = {"format", "hwupload", "tonemap", "zscale"} - {missing}
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv"),
        metadata=MediaProbe(
            video_stream_index=0,
            bit_depth=10,
            color_transfer="smpte2084",
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        capabilities=_capabilities(filters=filters),
    )
    monkeypatch.setattr(
        qsv_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    with pytest.raises(RuntimeError, match=rf"filters: {missing}"):
        asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))


def test_qsv_sdr_command_does_not_require_tonemap_filters(monkeypatch, tmp_path):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv"),
        metadata=MediaProbe(
            video_stream_index=0,
            bit_depth=8,
            color_transfer="bt709",
        ),
        capabilities=_capabilities(filters=("vpp_qsv",)),
    )
    monkeypatch.setattr(
        qsv_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    cmd = asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))

    assert cmd[cmd.index("-vf") + 1] == "vpp_qsv=format=nv12"


def test_qsv_device(monkeypatch):
    monkeypatch.setattr(
        qsv_module, "resolve_vaapi_device", AsyncMock(return_value=None)
    )
    context = TranscodeContext(options=TranscodeOptions(hwaccel="qsv"))

    with pytest.raises(RuntimeError, match="DRM render device"):
        asyncio.run(get_hwaccel("qsv").input_args(context))


def test_vaapi_args(monkeypatch):
    device = "/dev/dri/renderD128"
    strategy = get_hwaccel("vaapi")
    options = TranscodeOptions(hwaccel="vaapi", quality="high")
    context = TranscodeContext(options=options)

    monkeypatch.setattr(
        vaapi_module,
        "resolve_vaapi_device",
        AsyncMock(return_value=device),
    )

    assert asyncio.run(strategy.input_args(context)) == [
        "-hwaccel",
        "vaapi",
        "-hwaccel_output_format",
        "vaapi",
        "-vaapi_device",
        device,
    ]
    assert strategy.video_filters(context) == ["scale_vaapi=format=nv12"]

    scaled_context = TranscodeContext(
        options=TranscodeOptions(hwaccel="vaapi", resolution="720p"),
        metadata=MediaProbe(width=1920, height=1080),
    )
    assert asyncio.run(strategy.input_args(scaled_context)) == [
        "-hwaccel",
        "vaapi",
        "-vaapi_device",
        device,
    ]
    assert strategy.video_filters(scaled_context) == [
        "scale=1280:720",
        "setsar=1",
        "format=nv12",
        "hwupload",
    ]
    assert strategy.keyframe_args(context) == [
        "-force_key_frames:0",
        "expr:if(isnan(prev_forced_t),1,gte(t,prev_forced_t+6))",
        "-flags:v:0",
        "+cgop",
    ]


@pytest.mark.parametrize(
    ("quality", "bitrate", "bufsize"),
    [
        ("low", "1500k", "3000k"),
        ("medium", "3000k", "6000k"),
        ("high", "6000k", "12000k"),
    ],
)
def test_vaapi_auto_rate_control(quality, bitrate, bufsize):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="vaapi", quality=quality)
    )

    assert get_hwaccel("vaapi").encoder_args(context) == [
        "-b:v",
        bitrate,
        "-maxrate",
        bitrate,
        "-bufsize",
        bufsize,
    ]


def test_vaapi_hdr10_filters(monkeypatch):
    context = _hdr_context(hwaccel="vaapi", resolution="720p")
    context.hardware = base_module.HardwareRuntime(
        "/dev/dri/renderD128",
        True,
        True,
    )
    strategy = get_hwaccel("vaapi")
    monkeypatch.setattr(
        vaapi_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    args = asyncio.run(strategy.input_args(context))

    assert args[:4] == [
        "-hwaccel",
        "vaapi",
        "-hwaccel_output_format",
        "vaapi",
    ]
    assert strategy.video_filters(context) == [
        f"scale_vaapi=w={context.scale_width}:h={context.scale_height}",
        "setsar=1",
        "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709",
    ]


def test_vaapi_scaled_hdr10_probe_preserves_p010(monkeypatch, tmp_path):
    media = tmp_path / "input.mkv"
    media.write_bytes(b"video")
    device = "/dev/dri/renderD128"
    strategy = get_hwaccel("vaapi")
    monkeypatch.setattr(base_module, "require_hardware_encoder", AsyncMock())
    monkeypatch.setattr(
        base_module,
        "probe_hardware_decode",
        AsyncMock(return_value=True),
    )
    probe_transform = AsyncMock(return_value=True)
    monkeypatch.setattr(base_module, "probe_hardware_transform", probe_transform)
    monkeypatch.setattr(
        strategy,
        "resolve_device",
        AsyncMock(return_value=device),
    )
    context = _hdr_context(
        hwaccel="vaapi",
        resolution="720p",
        capabilities=_capabilities(),
    )
    context.metadata = replace(
        context.metadata,
        codec="hevc",
        profile="Main 10",
    )

    context.hardware = asyncio.run(strategy.prepare_hardware(context, str(media)))

    assert context.hardware == base_module.HardwareRuntime(device, True, True)
    transform_args = probe_transform.await_args.args[-1]
    assert transform_args[transform_args.index("-vf") + 1] == (
        "scale_vaapi=w=1280:h=720,setsar=1,hwdownload,format=p010le"
    )
    assert strategy.video_filters(context) == [
        "scale_vaapi=w=1280:h=720",
        "setsar=1",
        "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709",
    ]


def test_vaapi_hlg_filters(monkeypatch):
    context = _hdr_context(hwaccel="vaapi", transfer="arib-std-b67")
    strategy = get_hwaccel("vaapi")
    monkeypatch.setattr(
        vaapi_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    args = asyncio.run(strategy.input_args(context))
    filters = strategy.video_filters(context)

    assert "-hwaccel_output_format" not in args
    assert "tonemap=hable:desat=0" in filters
    assert filters[-2:] == ["format=nv12", "hwupload"]


def test_vaapi_falls_back_to_software_decoding(monkeypatch):
    strategy = get_hwaccel("vaapi")
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="vaapi"),
        capabilities=_capabilities(hwaccels=()),
    )
    monkeypatch.setattr(
        vaapi_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    args = asyncio.run(strategy.input_args(context))

    assert args == ["-vaapi_device", "/dev/dri/renderD128"]
    assert strategy.video_filters(context) == ["format=nv12", "hwupload"]


def test_vaapi_hdr10_fallback_uploads_for_tonemap(monkeypatch):
    context = _hdr_context(
        hwaccel="vaapi",
        capabilities=_capabilities(hwaccels=()),
    )
    monkeypatch.setattr(
        vaapi_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    args = asyncio.run(get_hwaccel("vaapi").input_args(context))
    filters = get_hwaccel("vaapi").video_filters(context)

    assert "-hwaccel" not in args
    assert filters[:2] == ["format=p010", "hwupload"]


def test_vaapi_device(monkeypatch):
    monkeypatch.setattr(
        vaapi_module, "resolve_vaapi_device", AsyncMock(return_value=None)
    )
    context = TranscodeContext(options=TranscodeOptions(hwaccel="vaapi"))

    with pytest.raises(RuntimeError, match="DRM render device"):
        asyncio.run(get_hwaccel("vaapi").input_args(context))


def test_videotoolbox_args():
    strategy = get_hwaccel("videotoolbox")
    options = TranscodeOptions(hwaccel="videotoolbox", quality="low")
    context = TranscodeContext(
        options=options,
        metadata=MediaProbe(
            avg_frame_rate=Fraction(24),
            r_frame_rate=Fraction(24),
            pixel_format="yuv420p",
        ),
    )

    assert asyncio.run(strategy.input_args(context)) == [
        "-hwaccel",
        "videotoolbox",
        "-hwaccel_output_format",
        "videotoolbox_vld",
    ]
    scaled_context = TranscodeContext(
        options=TranscodeOptions(hwaccel="videotoolbox", resolution="720p"),
        metadata=MediaProbe(width=1920, height=1080),
    )
    assert asyncio.run(strategy.input_args(scaled_context)) == [
        "-hwaccel",
        "videotoolbox",
    ]
    assert strategy.video_filters(context) == []
    unknown_format_context = TranscodeContext(options=options)
    assert strategy.video_filters(unknown_format_context) == ["scale_vt"]
    nonstandard_8_bit_context = TranscodeContext(
        options=options,
        metadata=MediaProbe(pixel_format="yuvj420p"),
    )
    assert strategy.video_filters(nonstandard_8_bit_context) == ["scale_vt"]
    ten_bit_context = TranscodeContext(
        options=options,
        metadata=MediaProbe(pixel_format="yuv420p10le"),
    )
    assert strategy.video_filters(ten_bit_context) == ["scale_vt"]
    scaled_ten_bit_context = TranscodeContext(
        options=scaled_context.options,
        metadata=MediaProbe(
            width=1920,
            height=1080,
            pixel_format="yuv420p10le",
        ),
    )
    assert strategy.video_filters(scaled_ten_bit_context) == [
        "scale=1280:720",
        "setsar=1",
        "format=nv12",
    ]
    assert strategy.encoder_args(context) == [
        "-b:v",
        "1500k",
        "-qmin",
        "-1",
        "-qmax",
        "-1",
        "-prio_speed",
        "1",
    ]
    assert strategy.keyframe_args(context) == [
        "-force_key_frames:0",
        "expr:if(isnan(prev_forced_t),1,gte(t,prev_forced_t+6))",
        "-flags:v:0",
        "+cgop",
        "-g:v:0",
        "144",
        "-keyint_min:v:0",
        "144",
    ]


@pytest.mark.parametrize(
    "metadata",
    [
        MediaProbe(),
        MediaProbe(
            avg_frame_rate=Fraction(6075, 271),
            r_frame_rate=Fraction(15),
        ),
    ],
)
def test_fixed_gop_is_omitted_without_stable_frame_rate(metadata):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="nvenc"),
        metadata=metadata,
    )

    args = get_hwaccel("nvenc").keyframe_args(context)

    assert args == [
        "-force_key_frames:0",
        "expr:if(isnan(prev_forced_t),1,gte(t,prev_forced_t+6))",
        "-flags:v:0",
        "+cgop",
    ]


@pytest.mark.parametrize(
    ("hwaccel", "option"),
    [("nvenc", "forced-idr"), ("qsv", "forced_idr")],
)
def test_independent_hardware_segments_require_forced_idr(hwaccel, option):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel),
        capabilities=_capabilities(encoder_options=()),
    )

    with pytest.raises(RuntimeError, match=rf"encoder options: {option}"):
        get_hwaccel(hwaccel).encoder_args(context)


def test_videotoolbox_sdr_normalization_uses_standard_scale_vt():
    strategy = get_hwaccel("videotoolbox")
    options = TranscodeOptions(hwaccel="videotoolbox")
    unknown = TranscodeContext(options=options)
    ten_bit = TranscodeContext(
        options=options,
        metadata=MediaProbe(pixel_format="yuv420p10le"),
    )

    assert strategy.video_filters(unknown) == ["scale_vt"]
    assert strategy.video_filters(ten_bit) == ["scale_vt"]


def test_videotoolbox_falls_back_to_software_decoding():
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel="videotoolbox"),
        metadata=MediaProbe(pixel_format="yuv420p10le"),
        capabilities=_capabilities(hwaccels=()),
    )
    strategy = get_hwaccel("videotoolbox")

    assert asyncio.run(strategy.input_args(context)) == []
    assert strategy.video_filters(context) == ["format=nv12"]


def test_videotoolbox_hdr_fallback_uses_software_tonemap():
    context = _hdr_context(
        hwaccel="videotoolbox",
        capabilities=_capabilities(hwaccels=()),
    )

    filters = get_hwaccel("videotoolbox").video_filters(context)

    assert "tonemap=hable:desat=0" in filters
    assert filters[-1] == "format=nv12"


@pytest.mark.parametrize(
    ("hwaccel", "available", "missing"),
    [
        (None, (), {"-preset", "-crf", "-profile:v"}),
        ("nvenc", ("forced-idr",), {"-preset"}),
        (
            "qsv",
            ("forced_idr",),
            {"-preset", "-mbbrc", "-rc_init_occupancy"},
        ),
        ("vaapi", (), {"-rc_mode", "-qp"}),
        ("videotoolbox", (), {"-prio_speed"}),
    ],
)
def test_encoder_args_omit_unavailable_private_options(hwaccel, available, missing):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel),
        capabilities=_capabilities(encoder_options=available),
    )

    args = get_hwaccel(hwaccel).encoder_args(context)

    assert missing.isdisjoint(args)


@pytest.mark.parametrize("transfer", ["smpte2084", "arib-std-b67"])
def test_videotoolbox_hdr_filters(transfer):
    context = _hdr_context(
        hwaccel="videotoolbox",
        resolution="720p",
        transfer=transfer,
    )
    strategy = get_hwaccel("videotoolbox")

    assert asyncio.run(strategy.input_args(context)) == [
        "-hwaccel",
        "videotoolbox",
        "-hwaccel_output_format",
        "videotoolbox_vld",
    ]
    assert strategy.video_filters(context) == [
        (
            f"scale_vt=w='{context.scale_width}':h='{context.scale_height}':"
            "color_matrix=bt709:color_primaries=bt709:color_transfer=bt709"
        ),
        "setsar=1",
    ]


@pytest.mark.parametrize(
    ("hwaccel", "device", "expected"),
    [
        (
            "vaapi",
            "/dev/dri/renderD128",
            [
                "transpose_vaapi=dir=clock",
                "scale_vaapi=w=592:h=1080",
                "setsar=1",
                "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709",
            ],
        ),
        (
            "videotoolbox",
            None,
            [
                "transpose_vt=dir=clock",
                (
                    "scale_vt=w='592':h='1080':color_matrix=bt709:"
                    "color_primaries=bt709:color_transfer=bt709"
                ),
                "setsar=1",
            ],
        ),
    ],
)
def test_hardware_rotated_hdr_scale_resets_sar(hwaccel, device, expected):
    context = _hdr_context(hwaccel=hwaccel, resolution="1080p")
    context.metadata = replace(
        context.metadata,
        width=1920,
        height=1080,
        rotation=90,
    )
    context.hardware = base_module.HardwareRuntime(device, True, True)

    assert get_hwaccel(hwaccel).video_filters(context) == expected


@pytest.mark.parametrize(
    ("quality", "bitrate"),
    [("low", "1500k"), ("medium", "3000k"), ("high", "6000k")],
)
def test_options_bitrate(quality, bitrate):
    assert TranscodeOptions(quality=quality).bitrate == bitrate


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quality": "/tmp/outside"},
        {"resolution": "../outside"},
        {"hwaccel": "invalid"},
    ],
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
    monkeypatch.setattr(transcoder, "is_complete", complete)
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", cleanup)
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
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(
        transcoder,
        "_probe_media",
        AsyncMock(
            return_value=MediaProbe(
                video_stream_index=0,
                audio_stream_index=1,
                duration=60.0,
                width=1920,
                height=1080,
            )
        ),
    )
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(
        transcoder,
        "load_ffmpeg_capabilities",
        AsyncMock(return_value=_capabilities()),
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


def test_capability_preflight_failure_releases_lock(monkeypatch, tmp_path):
    lock = object()
    release = Mock()
    create = AsyncMock(side_effect=AssertionError("transcode process started"))
    capabilities = _capabilities(encoders=("aac",))

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(
        transcoder,
        "_probe_media",
        AsyncMock(
            return_value=MediaProbe(
                video_stream_index=0,
                audio_stream_index=1,
                duration=60.0,
                width=1920,
                height=1080,
            )
        ),
    )
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(
        transcoder,
        "load_ffmpeg_capabilities",
        AsyncMock(return_value=capabilities),
    )
    monkeypatch.setattr(transcoder.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(transcoder, "_release_lock", release)

    with pytest.raises(RuntimeError, match="encoders: libx264"):
        asyncio.run(
            transcoder.ensure_transcode("input.mkv", "hash", TranscodeOptions())
        )

    create.assert_not_awaited()
    release.assert_called_once_with(lock)


def test_no_video_rejected_before_capability_discovery(monkeypatch, tmp_path):
    lock = object()
    release = Mock()
    load = AsyncMock(side_effect=AssertionError("capabilities queried"))

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(
        transcoder,
        "_probe_media",
        AsyncMock(return_value=MediaProbe(audio_stream_index=1, duration=60.0)),
    )
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(transcoder, "load_ffmpeg_capabilities", load)
    monkeypatch.setattr(transcoder, "_release_lock", release)

    with pytest.raises(RuntimeError, match="no transcodable video stream"):
        asyncio.run(
            transcoder.ensure_transcode("input.mkv", "hash", TranscodeOptions())
        )

    load.assert_not_awaited()
    release.assert_called_once_with(lock)


def test_dovi_only_rejected_before_capability_discovery(monkeypatch, tmp_path):
    lock = object()
    release = Mock()
    load = AsyncMock(side_effect=AssertionError("capabilities queried"))
    create = AsyncMock(side_effect=AssertionError("transcode process started"))
    metadata = MediaProbe(
        video_stream_index=0,
        dovi_profile=5,
        dovi_bl_present=True,
        dovi_bl_signal_compatibility_id=0,
    )

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(transcoder, "_probe_media", AsyncMock(return_value=metadata))
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(transcoder, "load_ffmpeg_capabilities", load)
    monkeypatch.setattr(transcoder.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(transcoder, "_release_lock", release)

    with pytest.raises(RuntimeError, match="Dolby Vision-only"):
        asyncio.run(
            transcoder.ensure_transcode("input.mkv", "hash", TranscodeOptions())
        )

    load.assert_not_awaited()
    create.assert_not_awaited()
    release.assert_called_once_with(lock)


def test_build_hls_cmd_rejects_dovi_only(tmp_path):
    context = TranscodeContext(
        options=TranscodeOptions(),
        metadata=MediaProbe(
            video_stream_index=0,
            dovi_profile=5,
            dovi_bl_present=True,
            dovi_bl_signal_compatibility_id=0,
        ),
    )

    with pytest.raises(RuntimeError, match="Dolby Vision-only"):
        asyncio.run(transcoder._build_hls_cmd("input.mkv", tmp_path, context))


@pytest.mark.parametrize(
    ("metadata", "options", "message"),
    [
        (
            MediaProbe(width=1920, height=1080, rotation=45),
            TranscodeOptions(),
            "Unsupported video rotation: 45",
        ),
        (
            MediaProbe(rotation=90),
            TranscodeOptions(),
            "requires valid width and height",
        ),
        (
            MediaProbe(),
            TranscodeOptions(resolution="720p"),
            "requires valid width and height",
        ),
        (
            MediaProbe(sample_aspect_ratio=(4, 3)),
            TranscodeOptions(),
            "requires valid width and height",
        ),
    ],
)
def test_supported_geometry_guard_rejects_invalid_plan(metadata, options, message):
    with pytest.raises(RuntimeError, match=message):
        transcoder._require_supported_geometry(metadata, options)


def test_supported_geometry_guard_allows_unknown_original_geometry():
    transcoder._require_supported_geometry(MediaProbe(), TranscodeOptions())


def test_rotation_rejected_before_capability_discovery(monkeypatch, tmp_path):
    lock = object()
    release = Mock()
    load = AsyncMock(side_effect=AssertionError("capabilities queried"))
    metadata = MediaProbe(
        video_stream_index=0,
        width=1920,
        height=1080,
        rotation=45,
    )

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(transcoder, "_probe_media", AsyncMock(return_value=metadata))
    monkeypatch.setattr(transcoder, "load_ffmpeg_capabilities", load)
    monkeypatch.setattr(transcoder, "_release_lock", release)

    with pytest.raises(RuntimeError, match="Unsupported video rotation: 45"):
        asyncio.run(
            transcoder.ensure_transcode("input.mkv", "hash", TranscodeOptions())
        )

    load.assert_not_awaited()
    release.assert_called_once_with(lock)


def test_hardware_preparation_precedes_build_and_start(monkeypatch, tmp_path):
    events = []
    lock = object()
    proc = SimpleNamespace(pid=123, returncode=None, stderr=None)
    metadata = MediaProbe(
        video_stream_index=0,
        duration=60.0,
        codec="h264",
        profile="High",
        bit_depth=8,
        pixel_format="yuv420p",
    )
    options = TranscodeOptions(hwaccel="nvenc")

    async def prepare(context, media_path):
        events.append("prepare")
        assert media_path == "input.mkv"
        context.hardware = base_module.HardwareRuntime("0", False)

    async def build(_media_path, _out_dir, context):
        events.append("build")
        assert context.hardware == base_module.HardwareRuntime("0", False)
        return ["ffmpeg"]

    async def create(*_args, **_kwargs):
        events.append("start")
        return proc

    async def register(*_args):
        events.append("register")
        return "hash:profile"

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(transcoder, "_probe_media", AsyncMock(return_value=metadata))
    monkeypatch.setattr(transcoder, "_prepare_hardware", prepare, raising=False)
    monkeypatch.setattr(transcoder, "_build_hls_cmd", build)
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(
        transcoder,
        "load_ffmpeg_capabilities",
        AsyncMock(return_value=_capabilities()),
    )
    monkeypatch.setattr(transcoder.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(transcoder, "register_task", register)
    monkeypatch.setattr(transcoder, "_start_monitor", Mock())
    monkeypatch.setattr(transcoder, "wait_segment", AsyncMock(return_value=True))

    result = asyncio.run(transcoder.ensure_transcode("input.mkv", "hash", options))

    assert result == ("hash", options.profile)
    assert events == ["prepare", "build", "start", "register"]


def test_hardware_preparation_failure_releases_lock(monkeypatch, tmp_path):
    lock = object()
    release = Mock()
    build = AsyncMock(side_effect=AssertionError("command built"))
    create = AsyncMock(side_effect=AssertionError("transcode process started"))
    register = AsyncMock(side_effect=AssertionError("task registered"))
    metadata = MediaProbe(
        video_stream_index=0,
        codec="h264",
        profile="High",
        bit_depth=8,
        pixel_format="yuv420p",
    )

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(transcoder, "_probe_media", AsyncMock(return_value=metadata))
    monkeypatch.setattr(
        transcoder,
        "_prepare_hardware",
        AsyncMock(side_effect=RuntimeError("encoder unavailable")),
        raising=False,
    )
    monkeypatch.setattr(transcoder, "_build_hls_cmd", build)
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(
        transcoder,
        "load_ffmpeg_capabilities",
        AsyncMock(return_value=_capabilities()),
    )
    monkeypatch.setattr(transcoder.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(transcoder, "register_task", register)
    monkeypatch.setattr(transcoder, "_release_lock", release)

    with pytest.raises(RuntimeError, match="encoder unavailable"):
        asyncio.run(
            transcoder.ensure_transcode(
                "input.mkv",
                "hash",
                TranscodeOptions(hwaccel="nvenc"),
            )
        )

    build.assert_not_awaited()
    create.assert_not_awaited()
    register.assert_not_awaited()
    release.assert_called_once_with(lock)


@pytest.mark.parametrize(
    ("avg_frame_rate", "expected_framerate"),
    [(Fraction(24), Fraction(24)), (None, None)],
)
def test_ensure_builds_context(
    monkeypatch, tmp_path, avg_frame_rate, expected_framerate
):
    lock = object()
    proc = SimpleNamespace(pid=123, returncode=None, stderr=None)
    metadata = MediaProbe(
        video_stream_index=0,
        audio_stream_index=1,
        duration=60.0,
        avg_frame_rate=avg_frame_rate,
        r_frame_rate=avg_frame_rate,
        pixel_format="yuv420p10le",
        bit_depth=10,
        height=1080,
        color_transfer="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
    )
    probe = AsyncMock(return_value=metadata)
    build = AsyncMock(return_value=["ffmpeg"])
    register = AsyncMock(return_value="hash:profile")
    options = TranscodeOptions()
    capabilities = _capabilities()

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: tmp_path)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
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
    monkeypatch.setattr(transcoder, "_build_hls_cmd", build)
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(
        transcoder,
        "load_ffmpeg_capabilities",
        AsyncMock(return_value=capabilities),
    )
    monkeypatch.setattr(
        transcoder.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    monkeypatch.setattr(transcoder, "register_task", register)
    monkeypatch.setattr(transcoder, "_start_monitor", Mock())
    monkeypatch.setattr(transcoder, "wait_segment", AsyncMock(return_value=True))

    result = asyncio.run(transcoder.ensure_transcode("input.mkv", "hash", options))

    assert result == ("hash", options.profile)
    probe.assert_awaited_once_with("input.mkv")
    await_args = build.await_args
    assert await_args is not None
    context = await_args.args[2]
    assert isinstance(context, TranscodeContext)
    assert context.options is options
    assert context.source_framerate == expected_framerate
    assert context.source_pixel_format == "yuv420p10le"
    assert context.source_height == 1080
    assert context.metadata is metadata
    assert context.capabilities is capabilities
    assert context.needs_tonemap is True
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

    asyncio.run(transcoder._terminate_ffmpeg(cast(asyncio.subprocess.Process, proc)))

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

        async def read(_size):
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
        completion = transcoder._start_monitor(
            cast(asyncio.subprocess.Process, proc), cast(FileLock, lock), "task"
        )
        task = next(iter(transcoder._MONITOR_TASKS))
        assert task in transcoder._MONITOR_TASKS

        await started.wait()
        await transcoder.shutdown_monitors()
        return task, completion, proc, lock

    task, completion, proc, lock = asyncio.run(run())

    assert task.cancelled()
    assert completion.result().state == "stopped"
    assert not transcoder._MONITOR_TASKS
    proc.terminate.assert_called_once_with()
    finish.assert_awaited_once_with("task", 255)
    release.assert_called_once_with(lock)


def test_monitor_errors_logged(monkeypatch):
    error = Mock()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("monitor failed")

    monkeypatch.setattr(transcoder, "_monitor_ffmpeg", fail)
    monkeypatch.setattr(transcoder.logger, "error", error)

    async def run():
        completion = transcoder._start_monitor(
            cast(asyncio.subprocess.Process, object()),
            cast(FileLock, object()),
            "task",
        )
        task = next(iter(transcoder._MONITOR_TASKS))
        assert task in transcoder._MONITOR_TASKS
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        return task, completion

    task, completion = asyncio.run(run())

    assert task not in transcoder._MONITOR_TASKS
    assert completion.result().state == "failed"
    assert completion.result().error is None
    error.assert_called_once()


def test_stderr_detail_redacts_media_and_output_paths():
    detail = transcoder._stderr_detail(
        (
            b"Cannot open /private/media/secret.mkv\n"
            b"/output/hash/segment_000000.ts failed\n"
        ),
        {
            "/private/media/secret.mkv": "<input>",
            "secret.mkv": "<input>",
            "/output/hash": "<output>",
        },
    )

    assert detail == "Cannot open <input>\n<output>/segment_000000.ts failed"


def test_stderr_detail_is_bounded_to_recent_lines():
    data = b"".join(f"error-{index:02d} {'x' * 500}\n".encode() for index in range(40))

    detail = transcoder._stderr_detail(data)

    assert detail is not None
    assert len(detail.encode()) <= 8 * 1024
    assert len(detail.splitlines()) <= 24
    assert "error-39" in detail
    assert "error-00" not in detail


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (0, "finished"),
        (255, "stopped"),
        (1, "failed"),
        (None, "failed"),
    ],
)
def test_ffmpeg_completion_classifies_exit(returncode, expected):
    completion = transcoder.FFmpegCompletion.from_exit(returncode, "detail")

    assert completion.returncode == returncode
    assert completion.state == expected
    assert completion.error == "detail"


def test_monitor_tail(monkeypatch):
    read = AsyncMock(side_effect=[b"a" * 400, b"b" * 400, b""])
    proc = SimpleNamespace(
        pid=123,
        returncode=1,
        stderr=SimpleNamespace(read=read),
        wait=AsyncMock(),
    )
    finish = AsyncMock()
    release = Mock()

    monkeypatch.setattr(transcoder, "finish_task", finish)
    monkeypatch.setattr(transcoder, "_release_lock", release)
    monkeypatch.setattr(transcoder.logger, "error", Mock())

    result = asyncio.run(
        transcoder._monitor_ffmpeg(
            cast(asyncio.subprocess.Process, proc),
            cast(FileLock, object()),
            "task",
        )
    )

    assert read.await_count == 3
    assert result == transcoder.FFmpegCompletion.from_exit(1, "a" * 400 + "b" * 400)
    finish.assert_awaited_once_with("task", 1, "a" * 400 + "b" * 400)


def test_monitor_completion_precedes_task_store_completion(monkeypatch):
    proc = SimpleNamespace(
        pid=123,
        returncode=1,
        stderr=SimpleNamespace(
            read=AsyncMock(side_effect=[b"device initialization failed\n", b""])
        ),
        wait=AsyncMock(),
    )
    release = Mock()
    persistence_started = asyncio.Event()
    allow_persistence = asyncio.Event()

    async def finish_task(*_args):
        persistence_started.set()
        await allow_persistence.wait()

    monkeypatch.setattr(transcoder, "finish_task", AsyncMock(side_effect=finish_task))
    monkeypatch.setattr(transcoder, "_release_lock", release)
    monkeypatch.setattr(transcoder.logger, "error", Mock())

    async def run():
        completion = transcoder._start_monitor(
            cast(asyncio.subprocess.Process, proc),
            cast(FileLock, object()),
            "task",
        )
        monitor = next(iter(transcoder._MONITOR_TASKS))
        await persistence_started.wait()
        assert completion.done()
        result = completion.result()
        allow_persistence.set()
        await monitor
        await asyncio.sleep(0)
        return result

    result = asyncio.run(run())

    assert result == transcoder.FFmpegCompletion.from_exit(
        1, "device initialization failed"
    )
    release.assert_called_once()


def test_monitor_discards_stderr_older_than_raw_tail(monkeypatch):
    read = AsyncMock(
        side_effect=[
            b"old root cause\n" + b"x" * (20 * 1024),
            b"\nlatest failure\n",
            b"",
        ]
    )
    proc = SimpleNamespace(
        pid=123,
        returncode=1,
        stderr=SimpleNamespace(read=read),
        wait=AsyncMock(),
    )
    monkeypatch.setattr(transcoder, "finish_task", AsyncMock())
    monkeypatch.setattr(transcoder, "_release_lock", Mock())
    monkeypatch.setattr(transcoder.logger, "error", Mock())

    result = asyncio.run(
        transcoder._monitor_ffmpeg(
            cast(asyncio.subprocess.Process, proc),
            cast(FileLock, object()),
            "task",
        )
    )

    assert result.error is not None
    assert "old root cause" not in result.error
    assert "latest failure" in result.error
    assert len(result.error.encode()) <= 8 * 1024
    assert len(result.error.splitlines()) <= 24


def test_monitor_completion_precedes_logging_failure(monkeypatch):
    proc = SimpleNamespace(
        pid=123,
        returncode=1,
        stderr=SimpleNamespace(
            read=AsyncMock(side_effect=[b"primary encoder failure\n", b""])
        ),
        wait=AsyncMock(),
    )
    release = Mock()
    monkeypatch.setattr(transcoder, "finish_task", AsyncMock())
    monkeypatch.setattr(transcoder, "_release_lock", release)
    monkeypatch.setattr(
        transcoder.logger,
        "error",
        Mock(side_effect=[RuntimeError("logging failed"), None]),
    )

    async def run():
        completion = transcoder._start_monitor(
            cast(asyncio.subprocess.Process, proc),
            cast(FileLock, object()),
            "task",
        )
        monitor = next(iter(transcoder._MONITOR_TASKS))
        await asyncio.gather(monitor, return_exceptions=True)
        await asyncio.sleep(0)
        return completion.result()

    result = asyncio.run(run())

    assert result == transcoder.FFmpegCompletion.from_exit(1, "primary encoder failure")
    release.assert_called_once()


def test_startup_failure_returns_ffmpeg_detail(monkeypatch, tmp_path):
    source = "/private/media/secret.mkv"
    out_dir = tmp_path / "hash" / "profile"
    lock = object()
    proc = SimpleNamespace(
        pid=123,
        returncode=1,
        stderr=SimpleNamespace(
            read=AsyncMock(
                side_effect=[
                    (
                        f"Cannot create compression session for {source}\n"
                        f"{out_dir}/segment_000000.ts was not written\n"
                    ).encode(),
                    b"",
                ]
            )
        ),
        wait=AsyncMock(),
    )
    finish = AsyncMock()
    release = Mock()
    options = TranscodeOptions()

    monkeypatch.setattr(transcoder, "output_dir", lambda _hash, _profile: out_dir)
    monkeypatch.setattr(transcoder, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(transcoder, "cleanup_stale_hls", Mock())
    monkeypatch.setattr(
        transcoder,
        "_probe_media",
        AsyncMock(return_value=MediaProbe(video_stream_index=0, duration=60.0)),
    )
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(
        transcoder,
        "load_ffmpeg_capabilities",
        AsyncMock(return_value=_capabilities()),
    )
    monkeypatch.setattr(
        transcoder.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    monkeypatch.setattr(
        transcoder, "register_task", AsyncMock(return_value="hash:profile")
    )
    monkeypatch.setattr(transcoder, "finish_task", finish)
    monkeypatch.setattr(transcoder, "wait_segment", AsyncMock(return_value=False))
    monkeypatch.setattr(transcoder, "_release_lock", release)
    monkeypatch.setattr(transcoder.logger, "error", Mock())

    async def run():
        try:
            return await transcoder.ensure_transcode(source, "hash", options)
        finally:
            await asyncio.gather(
                *tuple(transcoder._MONITOR_TASKS), return_exceptions=True
            )

    with pytest.raises(KaloscopeException) as caught:
        asyncio.run(run())

    assert type(caught.value) is KaloscopeException
    message = str(caught.value)
    assert "FFmpeg failed with code 1" in message
    assert "Cannot create compression session" in message
    assert source not in message
    assert Path(source).name not in message
    assert str(out_dir) not in message
    finish.assert_awaited_once_with(
        "hash:profile",
        1,
        (
            "Cannot create compression session for <input>\n"
            "<output>/segment_000000.ts was not written"
        ),
    )
    release.assert_called_once_with(lock)


def test_timeout_keeps_lock(monkeypatch, tmp_path):
    lock = object()
    proc = SimpleNamespace(pid=123, returncode=None, stderr=None)
    release = Mock()

    monkeypatch.setattr(
        transcoder, "output_dir", lambda _hash, profile: tmp_path / profile
    )
    monkeypatch.setattr(transcoder, "_acquire_lock", lambda _path: lock)
    monkeypatch.setattr(
        transcoder,
        "_probe_media",
        AsyncMock(
            return_value=MediaProbe(
                video_stream_index=0,
                audio_stream_index=1,
                duration=60.0,
                width=1920,
                height=1080,
            )
        ),
    )
    monkeypatch.setattr(
        transcoder, "_build_hls_cmd", AsyncMock(return_value=["ffmpeg"])
    )
    monkeypatch.setattr(transcoder, "_ffmpeg", AsyncMock(return_value="ffmpeg"))
    monkeypatch.setattr(
        transcoder,
        "load_ffmpeg_capabilities",
        AsyncMock(return_value=_capabilities()),
    )
    monkeypatch.setattr(
        transcoder.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    monkeypatch.setattr(
        transcoder, "register_task", AsyncMock(return_value="hash:profile")
    )
    monkeypatch.setattr(transcoder, "wait_segment", AsyncMock(return_value=False))
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
        asyncio.run(
            transcoder._monitor_ffmpeg(
                cast(asyncio.subprocess.Process, proc),
                cast(FileLock, lock),
                "task",
            )
        )

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
    monkeypatch.setattr(tasks, "remove_endlist", remove_endlist)

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
            "process_start_id": "original-process",
        }
    }

    def kill(_pid, _signal):
        assert lock.locked is False

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, lock))
    monkeypatch.setattr(
        tasks, "_process_start_id", AsyncMock(return_value="original-process")
    )
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


def test_delete_keeps_locked(monkeypatch, tmp_path):
    out_dir = tmp_path / "hash" / "profile"
    out_dir.mkdir(parents=True)
    playlist = out_dir / "index.m3u8"
    playlist.write_text("#EXTM3U\n")
    store = {
        "hash:profile": {
            **_runtime_task(out_dir, tasks.TaskState.FINISHED),
            "out_dir": str(out_dir),
        }
    }
    lock = transcoder._acquire_lock(out_dir)
    assert lock is not None

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))

    try:
        result = asyncio.run(tasks.delete_tasks(["hash:profile"]))
    finally:
        transcoder._release_lock(lock)

    assert result == []
    assert "hash:profile" in store
    assert playlist.is_file()


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


@pytest.mark.parametrize(
    ("state", "returncode", "expected"),
    [
        (tasks.TaskState.RUNNING, 0, tasks.TaskState.FINISHED),
        (tasks.TaskState.RUNNING, 255, tasks.TaskState.STOPPED),
        (tasks.TaskState.STOPPING, 1, tasks.TaskState.STOPPED),
    ],
)
def test_finish_states(monkeypatch, tmp_path, state, returncode, expected):
    store = {"task": _runtime_task(tmp_path, state)}
    remove = Mock()

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(tasks, "remove_endlist", remove)

    asyncio.run(tasks.finish_task("task", returncode))

    assert store["task"]["state"] == expected
    assert store["task"]["finished_at"] is not None
    if returncode == 0:
        remove.assert_not_called()
    else:
        remove.assert_called_once_with(str(tmp_path))


@pytest.mark.parametrize(
    ("complete", "expected"),
    [
        (True, tasks.TaskState.FINISHED),
        (False, tasks.TaskState.STOPPED),
    ],
)
def test_stop_missing(monkeypatch, tmp_path, complete, expected):
    store = {"task": _runtime_task(tmp_path)}
    remove = Mock()

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(
        tasks, "_process_start_id", AsyncMock(return_value="original-process")
    )
    monkeypatch.setattr(tasks.os, "kill", Mock(side_effect=ProcessLookupError))
    monkeypatch.setattr(tasks, "is_complete", Mock(return_value=complete))
    monkeypatch.setattr(tasks, "remove_endlist", remove)

    result = asyncio.run(tasks.stop_tasks(["task"]))

    assert result == ["task"]
    assert store["task"]["state"] == expected
    assert store["task"]["finished_at"] is not None
    if complete:
        remove.assert_not_called()
    else:
        remove.assert_called_once_with(str(tmp_path))


def test_stop_does_not_signal_reused_pid(monkeypatch, tmp_path):
    store = {"task": _runtime_task(tmp_path)}
    kill = Mock()

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(
        tasks,
        "_process_start_id",
        AsyncMock(return_value="replacement-process"),
        raising=False,
    )
    monkeypatch.setattr(tasks.os, "kill", kill)
    monkeypatch.setattr(tasks, "is_complete", Mock(return_value=False))
    monkeypatch.setattr(tasks, "remove_endlist", Mock())

    result = asyncio.run(tasks.stop_tasks(["task"]))

    assert result == ["task"]
    assert store["task"]["state"] == tasks.TaskState.STOPPED
    assert store["task"]["finished_at"] is not None
    kill.assert_not_called()


def test_stop_refuses_task_without_process_start_id(monkeypatch, tmp_path):
    task = _runtime_task(tmp_path)
    task["process_start_id"] = None
    store = {"task": task}
    kill = Mock()

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(tasks.os, "kill", kill)

    with pytest.raises(RuntimeError, match="Cannot safely identify"):
        asyncio.run(tasks.stop_tasks(["task"]))

    assert store["task"]["state"] == tasks.TaskState.RUNNING
    kill.assert_not_called()


def test_stop_windows_skips_process_identity_check(monkeypatch, tmp_path):
    task = _runtime_task(tmp_path)
    task["process_start_id"] = None
    store = {"task": task}
    process_start_id = AsyncMock()
    kill = Mock()

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(tasks, "_process_start_id", process_start_id)
    monkeypatch.setattr(tasks.sys, "platform", "win32")
    monkeypatch.delattr(tasks.signal, "SIGKILL")
    monkeypatch.setattr(tasks.os, "kill", kill)

    result = asyncio.run(tasks.stop_tasks(["task"]))

    assert result == ["task"]
    assert store["task"]["state"] == tasks.TaskState.STOPPING
    process_start_id.assert_not_awaited()
    kill.assert_called_once_with(123, tasks.signal.SIGTERM)


def test_stop_rollback(monkeypatch, tmp_path):
    store = {"task": _runtime_task(tmp_path)}

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(
        tasks, "_process_start_id", AsyncMock(return_value="original-process")
    )
    monkeypatch.setattr(tasks.os, "kill", Mock(side_effect=PermissionError))

    with pytest.raises(PermissionError):
        asyncio.run(tasks.stop_tasks(["task"]))

    assert store["task"]["state"] == tasks.TaskState.RUNNING


def test_stop_restores_pending(monkeypatch, tmp_path):
    store = {}
    for pid in (1, 2, 3):
        task = _runtime_task(tmp_path / str(pid))
        task["pid"] = pid
        store[str(pid)] = task

    killed = []

    def kill(pid, _signal):
        killed.append(pid)
        if pid == 2:
            raise PermissionError

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(
        tasks, "_process_start_id", AsyncMock(return_value="original-process")
    )
    monkeypatch.setattr(tasks.os, "kill", kill)

    with pytest.raises(PermissionError):
        asyncio.run(tasks.stop_tasks(["1", "2", "3"]))

    assert killed == [1, 2]
    assert store["1"]["state"] == tasks.TaskState.STOPPING
    assert store["2"]["state"] == tasks.TaskState.RUNNING
    assert store["3"]["state"] == tasks.TaskState.RUNNING


@pytest.mark.parametrize("state", [tasks.TaskState.RUNNING, tasks.TaskState.STOPPING])
def test_delete_active(monkeypatch, tmp_path, state):
    store = {"hash:profile": _runtime_task(tmp_path, state)}
    delete = Mock()

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
    monkeypatch.setattr(tasks, "delete_output", delete)

    result = asyncio.run(tasks.delete_tasks(["hash:profile"]))

    assert result == []
    assert "hash:profile" in store
    delete.assert_not_called()
