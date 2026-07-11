from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

import aiofiles
from sanic.log import logger

from app.core.config import KaloscopeConfig
from app.core.constants import ENCODING
from app.core.transcode.options import (
    ENCODER_CONFIG,
    QUALITY_CRF,
    RESOLUTION_MAX_HEIGHT,
    HWAccelType,
    QualityLevel,
    ResolutionLimit,
)

if TYPE_CHECKING:
    from app.core.transcode.tasks import TaskSnapshot

_SEGMENT_WAIT_TIMEOUT = 30.0
_SEGMENT_WAIT_INTERVAL = 0.25

_EXTINF_RE = re.compile(r"^#EXTINF:([0-9]+(?:\.[0-9]+)?)", re.MULTILINE)
"""Regular expression to extract segment durations from HLS playlists."""

_SEGMENT_LINE_RE = re.compile(r"^(?!\s*#)(.+\.ts)\s*$", re.MULTILINE)
"""Regex to detect if an M3U8 playlist contains at least one segment line."""

_MINIMAL_M3U8 = (
    "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
)


class ProfileTags(TypedDict):
    """Validated tags parsed from a transcode profile name."""

    quality: QualityLevel | None
    resolution: ResolutionLimit | None
    hwaccel: HWAccelType | None


@dataclass
class TranscodeStats:
    """Derived statistics for a transcoded HLS output directory."""

    finished: bool = False
    duration: float = 0.0
    segments: int = 0
    size: int = 0
    progress: int | None = None
    updated_at: str | None = None


def estimate_progress(encoded_duration: float, duration: float | None) -> int | None:
    """Estimate transcode progress from encoded and source durations.

    Args:
        encoded_duration: The duration already encoded, in seconds.
        duration: The total source duration in seconds, if known.

    Returns:
        The estimated percentage capped at 99, or `None` if unavailable.
    """
    if duration and duration > 0 and encoded_duration > 0:
        return min(99, max(0, int(encoded_duration / duration * 100)))
    return None


def parse_profile(profile: str) -> ProfileTags:
    """Parse a transcode profile directory name into UI tags.

    Profiles are generated as `{quality}_{resolution}_{hwaccel}` by
    `TranscodeOptions.profile`. Unknown profile shapes return empty fields so
    callers can still display the raw profile as a fallback tag.

    Args:
        profile: The transcode profile directory name.

    Returns:
        The parsed quality, resolution, and hardware acceleration tags.
    """
    quality, sep1, rest = profile.partition("_")
    resolution, sep2, hwaccel = rest.rpartition("_")
    if not sep1 or not sep2:
        return {"quality": None, "resolution": None, "hwaccel": None}
    if quality not in QUALITY_CRF or resolution not in RESOLUTION_MAX_HEIGHT:
        return {"quality": None, "resolution": None, "hwaccel": None}

    hwaccel = hwaccel.lower()
    valid_hwaccels = {*ENCODER_CONFIG.keys(), "software", "none", "null"}
    if hwaccel not in valid_hwaccels:
        return {"quality": None, "resolution": None, "hwaccel": None}

    return {
        "quality": cast(QualityLevel, quality),
        "resolution": cast(ResolutionLimit, resolution),
        "hwaccel": (
            None
            if hwaccel in ("none", "null", "software")
            else cast(HWAccelType, hwaccel)
        ),
    }


def output_dir(media_hash: str, profile: str) -> Path:
    """Get the deterministic output directory for the transcoded HLS files.

    Args:
        media_hash: The media file hash.
        profile: The transcode profile identifier.

    Returns:
        The output directory path.
    """
    return Path(KaloscopeConfig.get_workspace("transcoded")) / media_hash / profile


def output_stats(out_dir: Path | str, duration: float | None = None) -> TranscodeStats:
    """Read HLS output files and derive progress information.

    Args:
        out_dir: The HLS output directory.
        duration: The total source duration in seconds, if known.

    Returns:
        Statistics derived from the output files and playlist.
    """
    out_dir = Path(out_dir)
    stats = TranscodeStats()
    if not out_dir.is_dir():
        return stats

    for path in out_dir.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                stats.size += path.stat().st_size

    m3u8_path = out_dir / "index.m3u8"
    if not m3u8_path.is_file():
        return stats

    try:
        content = m3u8_path.read_text(encoding=ENCODING)
        stat = m3u8_path.stat()
    except OSError:
        return stats

    segments = [float(match.group(1)) for match in _EXTINF_RE.finditer(content)]
    stats.finished = "#EXT-X-ENDLIST" in content
    stats.duration = round(sum(segments), 3)
    stats.segments = len(segments)
    stats.updated_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()

    if stats.finished:
        stats.progress = 100
    else:
        stats.progress = estimate_progress(stats.duration, duration)

    return stats


def scan_outputs(
    root: Path | str | None = None, *, exclude_ids: set[str] | None = None
) -> list[TaskSnapshot]:
    """Scan the transcoded workspace for finished and interrupted HLS outputs.

    Args:
        root: The transcode root directory, or `None` to use the workspace.
        exclude_ids: Task IDs to skip before reading their output files.

    Returns:
        Task snapshots derived from HLS output directories.
    """
    root = (
        Path(root)
        if root is not None
        else Path(KaloscopeConfig.get_workspace("transcoded"))
    )
    if not root.is_dir():
        return []

    from app.core.transcode.tasks import TaskState

    tasks: list[TaskSnapshot] = []
    for hash_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        media_hash = hash_dir.name
        for profile_dir in sorted(path for path in hash_dir.iterdir() if path.is_dir()):
            profile = profile_dir.name
            task_id = f"{media_hash}:{profile}"
            if exclude_ids is not None and task_id in exclude_ids:
                continue
            stats = output_stats(profile_dir)
            if not stats.finished and stats.segments == 0:
                continue
            profile_tags = parse_profile(profile)
            state = TaskState.FINISHED if stats.finished else TaskState.STOPPED
            tasks.append(
                {
                    "id": task_id,
                    "name": f"{media_hash}/{profile}",
                    "path": None,
                    "hash": media_hash,
                    "state": state,
                    "progress": 100 if stats.finished else stats.progress,
                    "duration": stats.duration if stats.finished else None,
                    "encoded_duration": stats.duration,
                    "encoded_segments": stats.segments,
                    "encoded_size": stats.size,
                    "pid": None,
                    "profile": profile,
                    "quality": profile_tags["quality"],
                    "resolution": profile_tags["resolution"],
                    "hwaccel": profile_tags["hwaccel"],
                    "started_at": None,
                    "finished_at": stats.updated_at,
                    "error_msg": None,
                }
            )
    return tasks


def delete_output(
    media_hash: str, profile: str, root: Path | str | None = None
) -> bool:
    """Delete a deterministic transcode output directory.

    Args:
        media_hash: The source media hash directory name.
        profile: The transcode profile directory name.
        root: The transcode root directory, or `None` to use the workspace.

    Returns:
        `True` if the output directory existed and was deleted.
    """
    root = (
        Path(root)
        if root is not None
        else Path(KaloscopeConfig.get_workspace("transcoded"))
    )
    out_dir = root / media_hash / profile
    try:
        root_resolved = root.resolve()
        out_resolved = out_dir.resolve()
    except OSError:
        return False
    if not out_resolved.is_relative_to(root_resolved) or not out_dir.is_dir():
        return False

    shutil.rmtree(out_dir)
    with contextlib.suppress(OSError):
        out_dir.parent.rmdir()
    return True


def _remove_endlist(out_dir: Path | str | None) -> None:
    """Remove a completion marker from an interrupted HLS playlist.

    Args:
        out_dir: The HLS output directory, or `None` if unavailable.
    """
    if out_dir is None:
        return
    m3u8_path = Path(out_dir) / "index.m3u8"
    if not m3u8_path.is_file():
        return
    try:
        content = m3u8_path.read_text(encoding=ENCODING)
        original_lines = content.splitlines()
        lines = [line for line in original_lines if line.strip() != "#EXT-X-ENDLIST"]
        if len(lines) == len(original_lines):
            return
        content = "\n".join(lines)
        if content:
            content += "\n"
        m3u8_path.write_text(content, encoding=ENCODING)
    except OSError:
        logger.warning("Failed to remove HLS ENDLIST marker: %s", m3u8_path)


def _cleanup_stale_hls(out_dir: Path):
    """Remove stale HLS files before rebuilding an incomplete transcode.

    Args:
        out_dir: The output directory to clean.
    """
    targets = [
        out_dir / "index.m3u8",
        out_dir / "index.m3u8.tmp",
        *out_dir.glob("segment_*.ts"),
        *out_dir.glob("segment_*.ts.tmp"),
    ]
    for path in targets:
        if path.is_file():
            path.unlink()


def _is_complete(m3u8_path: Path) -> bool:
    """Check whether the M3U8 playlist file exists and contains the endlist tag.

    Args:
        m3u8_path: The M3U8 file path.

    Returns:
        `True` if the file exists and contains `#EXT-X-ENDLIST`,
        `False` otherwise.
    """
    if not m3u8_path.is_file():
        return False
    try:
        return "#EXT-X-ENDLIST" in m3u8_path.read_text()
    except Exception:
        return False


async def _wait_segment(
    m3u8_path: Path,
    proc: asyncio.subprocess.Process | None = None,
    timeout: float = _SEGMENT_WAIT_TIMEOUT,
    interval: float = _SEGMENT_WAIT_INTERVAL,
) -> bool:
    """Block until `m3u8_path` exists and contains at least one segment.

    Args:
        m3u8_path: The M3U8 file path.
        proc: The ffmpeg subprocess to watch.
        timeout: The max seconds to wait.
        interval: The polling interval in seconds.

    Returns:
        `True` if a segment was detected within the timeout, `False` otherwise.
    """
    elapsed = 0.0
    while elapsed < timeout:
        if m3u8_path.is_file():
            try:
                async with aiofiles.open(m3u8_path, encoding=ENCODING) as f:
                    content = await f.read()
            except Exception:
                await asyncio.sleep(interval)
                elapsed += interval
                continue
            if _SEGMENT_LINE_RE.search(content.strip()):
                return True

        # no more segments can be produced after ffmpeg exits
        if proc is not None and proc.returncode is not None:
            return False

        await asyncio.sleep(interval)
        elapsed += interval
    return False


async def read_m3u8(m3u8_path: Path) -> str | None:
    """Read an M3U8 playlist file.

    Args:
        m3u8_path: The M3U8 file path.

    Returns:
        The M3U8 text, `None` if the output directory doesn't exist,
        or a minimal playlist if the M3U8 file isn't ready yet.
    """
    if not m3u8_path.is_file():
        return _MINIMAL_M3U8 if m3u8_path.parent.is_dir() else None

    try:
        async with aiofiles.open(m3u8_path, encoding=ENCODING) as f:
            content = await f.read()
    except Exception:
        return _MINIMAL_M3U8

    return content if content.strip() else _MINIMAL_M3U8
