"""Unit tests for core transcoding."""

import asyncio
import importlib
import threading
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from filelock import FileLock

import app.core.transcode.hwaccels.qsv as qsv_module
import app.core.transcode.hwaccels.vaapi as vaapi_module
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
        "profile": "profile",
        "quality": "medium",
        "resolution": "original",
        "hwaccel": None,
        "out_dir": str(out_dir),
        "started_at": "2026-01-01",
        "finished_at": None,
        "error_msg": None,
    }


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
                b'"pix_fmt":"yuv420p10le","height":1080,'
                b'"color_range":"tv",'
                b'"color_transfer":"smpte2084","color_primaries":"bt2020",'
                b'"color_space":"bt2020nc",'
                b'"side_data_list":[{"side_data_type":"DOVI configuration record",'
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
        framerate=pytest.approx(30000 / 1001),
        pixel_format="yuv420p10le",
        height=1080,
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
        "bits_per_sample,bits_per_raw_sample,avg_frame_rate,pix_fmt,height,"
        "color_range,color_transfer,color_primaries,color_space:"
        "stream_disposition=attached_pic:stream_side_data=side_data_type,"
        "dv_profile,bl_present_flag,dv_bl_signal_compatibility_id"
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
            MediaProbe(video_stream_index=0, framerate=24.0),
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
    metadata = MediaProbe(framerate=23.5, pixel_format="yuv420p10le", height=1080)
    context = TranscodeContext(options=options, metadata=metadata)

    assert context.options is options
    assert context.metadata is metadata
    assert context.source_framerate == 23.5
    assert context.source_pixel_format == "yuv420p10le"
    assert context.source_height == 1080
    assert options.segment_length == 6
    assert "segment_length" not in {field.name for field in fields(TranscodeOptions)}
    assert context.needs_scale is True
    assert context.scale_height == "trunc(min(720,ih)/2)*2"
    assert context.scale_width == ("max(trunc(iw*trunc(min(720,ih)/2)*2/ih/16)*16,16)")
    assert context.encoder_config is options.encoder_config


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
    ("resolution", "source_height", "expected"),
    [
        ("original", 2160, False),
        ("1080p", 720, False),
        ("720p", 1080, True),
        ("720p", None, True),
    ],
)
def test_context_needs_scale_uses_source_height(resolution, source_height, expected):
    context = TranscodeContext(
        options=TranscodeOptions(resolution=resolution),
        metadata=MediaProbe(height=source_height),
    )

    assert context.needs_scale is expected


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
        "format",
        "hwupload",
        "hwupload_cuda",
        "scale",
        "scale_cuda",
        "scale_vaapi",
        "scale_vt",
        "tonemap",
        "tonemap_vaapi",
        "vpp_qsv",
        "zscale",
    ),
    encoder_options=(
        "crf",
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
            framerate=24.0,
            pixel_format="yuv420p10le",
            bit_depth=10,
            height=2160,
            color_transfer=transfer,
            color_primaries="bt2020",
            color_space="bt2020nc",
        ),
        capabilities=capabilities,
    )


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
    assert "-hls_flags" not in cmd
    assert "-vf" not in cmd


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
        metadata=MediaProbe(framerate=23.5, pixel_format="yuv420p"),
    )

    assert asyncio.run(strategy.input_args(context)) == [
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
    ]
    scaled_context = TranscodeContext(
        options=TranscodeOptions(hwaccel="nvenc", resolution="720p")
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
        metadata=MediaProbe(pixel_format="yuv420p10le"),
    )
    assert strategy.video_filters(scaled_ten_bit_context) == []
    assert strategy.encoder_args(context) == [
        "-preset",
        "p7",
        "-b:v",
        "6000k",
        "-maxrate",
        "6000k",
        "-bufsize",
        "12000k",
    ]
    assert strategy.keyframe_args(context) == [
        "-g:v:0",
        "141",
        "-keyint_min:v:0",
        "141",
    ]


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
        metadata=MediaProbe(framerate=25.0),
    )
    scaled_context = TranscodeContext(
        options=TranscodeOptions(hwaccel="qsv", resolution="720p")
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
    assert "-hwaccel" not in asyncio.run(strategy.input_args(scaled_context))
    assert strategy.video_filters(context) == ["vpp_qsv=format=nv12"]
    assert strategy.video_filters(scaled_context) == ["format=nv12"]
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
    ]
    assert strategy.keyframe_args(context) == [
        "-g:v:0",
        "150",
        "-keyint_min:v:0",
        "150",
    ]


@pytest.mark.parametrize("transfer", ["smpte2084", "arib-std-b67"])
def test_qsv_hdr_filters(monkeypatch, transfer):
    context = _hdr_context(hwaccel="qsv", resolution="720p", transfer=transfer)
    strategy = get_hwaccel("qsv")
    monkeypatch.setattr(
        qsv_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    args = asyncio.run(strategy.input_args(context))

    assert args[-6:] == [
        "-hwaccel",
        "qsv",
        "-hwaccel_device",
        "qs",
        "-hwaccel_output_format",
        "qsv",
    ]
    assert strategy.video_filters(context) == [
        (
            "vpp_qsv=tonemap=1:format=nv12:out_color_matrix=bt709:"
            "out_color_primaries=bt709:out_color_transfer=bt709:"
            f"w='{context.scale_width}':h='{context.scale_height}'"
        )
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


def test_qsv_hdr_fallback_uploads_for_vpp(monkeypatch):
    context = _hdr_context(
        hwaccel="qsv",
        capabilities=_capabilities(hwaccels=()),
    )
    monkeypatch.setattr(
        qsv_module,
        "resolve_vaapi_device",
        AsyncMock(return_value="/dev/dri/renderD128"),
    )

    args = asyncio.run(get_hwaccel("qsv").input_args(context))
    filters = get_hwaccel("qsv").video_filters(context)

    assert "-hwaccel" not in args
    assert filters[:2] == ["format=p010le", "hwupload"]


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
        options=TranscodeOptions(hwaccel="vaapi", resolution="720p")
    )
    assert asyncio.run(strategy.input_args(scaled_context)) == [
        "-hwaccel",
        "vaapi",
        "-vaapi_device",
        device,
    ]
    assert strategy.video_filters(scaled_context) == ["format=nv12", "hwupload"]
    assert strategy.keyframe_args(context) == [
        "-force_key_frames:0",
        "expr:gte(t,n_forced*6)",
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
        f"scale_vaapi=w='{context.scale_width}':h='{context.scale_height}'",
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
        metadata=MediaProbe(pixel_format="yuv420p"),
    )

    assert asyncio.run(strategy.input_args(context)) == [
        "-hwaccel",
        "videotoolbox",
        "-hwaccel_output_format",
        "videotoolbox_vld",
    ]
    scaled_context = TranscodeContext(
        options=TranscodeOptions(hwaccel="videotoolbox", resolution="720p")
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
        metadata=MediaProbe(pixel_format="yuv420p10le"),
    )
    assert strategy.video_filters(scaled_ten_bit_context) == []
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
        "expr:gte(t,n_forced*6)",
        "-g:v:0",
        "180",
        "-keyint_min:v:0",
        "180",
    ]


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
    ("hwaccel", "missing"),
    [
        (None, {"-preset", "-crf", "-profile:v"}),
        ("nvenc", {"-preset"}),
        ("qsv", {"-preset", "-mbbrc", "-rc_init_occupancy"}),
        ("vaapi", {"-rc_mode", "-qp"}),
        ("videotoolbox", {"-prio_speed"}),
    ],
)
def test_encoder_args_omit_unavailable_private_options(hwaccel, missing):
    context = TranscodeContext(
        options=TranscodeOptions(hwaccel=hwaccel),
        capabilities=_capabilities(encoder_options=()),
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
        )
    ]


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
    ("framerate", "expected_framerate"),
    [(24.0, 24.0), (None, 30.0)],
)
def test_ensure_builds_context(monkeypatch, tmp_path, framerate, expected_framerate):
    lock = object()
    proc = SimpleNamespace(pid=123, returncode=None, stderr=None)
    metadata = MediaProbe(
        video_stream_index=0,
        audio_stream_index=1,
        duration=60.0,
        framerate=framerate,
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
        task = transcoder._start_monitor(
            cast(asyncio.subprocess.Process, proc), cast(FileLock, lock), "task"
        )
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
        task = transcoder._start_monitor(
            cast(asyncio.subprocess.Process, object()),
            cast(FileLock, object()),
            "task",
        )
        assert task in transcoder._MONITOR_TASKS
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        return task

    task = asyncio.run(run())

    assert task not in transcoder._MONITOR_TASKS
    error.assert_called_once()


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

    asyncio.run(
        transcoder._monitor_ffmpeg(
            cast(asyncio.subprocess.Process, proc),
            cast(FileLock, object()),
            "task",
        )
    )

    assert read.await_count == 3
    finish.assert_awaited_once_with("task", 1, "a" * 100 + "b" * 400)


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


def test_stop_rollback(monkeypatch, tmp_path):
    store = {"task": _runtime_task(tmp_path)}

    monkeypatch.setattr(tasks, "_task_store", lambda: (store, _Lock()))
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
