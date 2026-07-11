import asyncio
import contextlib
import json
from dataclasses import replace
from pathlib import Path

from filelock import FileLock, Timeout
from sanic.log import logger

from app.core.transcode.hls import (
    cleanup_stale_hls,
    is_complete,
    output_dir,
    wait_segment,
)
from app.core.transcode.options import TranscodeOptions
from app.core.transcode.tasks import finish_task, register_task

_PROCESS_TERMINATE_TIMEOUT = 5.0
_MONITOR_TASKS: set[asyncio.Task[None]] = set()


async def _ffmpeg() -> str:
    """Get the ffmpeg executable path from global config or default to "ffmpeg".

    Returns:
        The ffmpeg executable name or path.
    """
    from app.models.general import GlobalConfig

    path = await GlobalConfig.get_or_none(key="ffmpeg.path")
    if path and isinstance(path.value, str) and Path(path.value).is_file():
        return path.value
    return "ffmpeg"


async def _ffprobe() -> str:
    """Resolve ffprobe next to a configured ffmpeg or use "ffprobe".

    Returns:
        The ffprobe executable name or path.
    """
    ffmpeg = await _ffmpeg()
    if ffmpeg != "ffmpeg":
        path = Path(ffmpeg).with_name("ffprobe")
        if path.is_file():
            return str(path)
    return "ffprobe"


async def _probe_media(media_path: str) -> tuple[float | None, float | None]:
    """Probe media duration and first-video-stream framerate via ffprobe.

    Args:
        media_path: The media file path to probe.

    Returns:
        A `(duration, framerate)` tuple. Each value is `None` when ffprobe
        exits unsuccessfully or the corresponding field is invalid.

    Raises:
        OSError: If the ffprobe process cannot be started.
        UnicodeDecodeError: If ffprobe output is not valid UTF-8.
    """
    proc = await asyncio.create_subprocess_exec(
        await _ffprobe(),
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration:stream=avg_frame_rate",
        "-of",
        "json",
        media_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None, None
    try:
        data = json.loads(stdout.decode())
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None

    duration = None
    format_data = data.get("format")
    if isinstance(format_data, dict):
        raw_duration = format_data.get("duration")
        if isinstance(raw_duration, (str, int, float)):
            with contextlib.suppress(ValueError, TypeError):
                duration = float(raw_duration)

    framerate = None
    streams = data.get("streams")
    if isinstance(streams, list) and streams and isinstance(streams[0], dict):
        raw = streams[0].get("avg_frame_rate")
        if isinstance(raw, str):
            try:
                num, _, den = raw.partition("/")
                value = float(num) / float(den) if den else float(num)
                if value > 0:
                    framerate = value
            except (ValueError, TypeError, ZeroDivisionError):
                pass
    return duration, framerate


async def probe_duration(media_path: str) -> float | None:
    """Probe the media file duration in seconds via ffprobe.

    Args:
        media_path: The media file path to probe.

    Returns:
        Duration in seconds, or `None` if ffprobe exits unsuccessfully or
        returns a non-numeric duration.

    Raises:
        OSError: If the ffprobe process cannot be started.
        UnicodeDecodeError: If ffprobe output is not valid UTF-8.
    """
    duration, _ = await _probe_media(media_path)
    return duration


async def probe_framerate(media_path: str) -> float | None:
    """Probe the average framerate of the media's first video stream via ffprobe.

    The framerate is reported by ffprobe as a rational string (e.g. `"30000/1001"`)
    and is parsed into a float here. It is used to approximate a one-segment GOP
    for hardware encoders when the input has a constant frame rate.

    Args:
        media_path: The media file path to probe.

    Returns:
        Frames per second, or `None` if ffprobe exits unsuccessfully or returns
        an invalid or non-positive value.

    Raises:
        OSError: If the ffprobe process cannot be started.
        UnicodeDecodeError: If ffprobe output is not valid UTF-8.
    """
    _, framerate = await _probe_media(media_path)
    return framerate


async def ensure_transcode(
    media_path: str, media_hash: str, options: TranscodeOptions
) -> tuple[str, str]:
    """Ensure the media file has been transcoded to HLS for the given profile.

    If the M3U8 playlist already exists and is complete, returns immediately.
    If another process is already transcoding, waits for at least one segment.
    Otherwise acquires the lock and starts an ffmpeg subprocess, waiting for
    the first segment before returning so playback can begin immediately.

    Args:
        media_path: The source media file path.
        media_hash: The media file hash used as part of the output path.
        options: The transcode parameters (encoder, quality, resolution).

    Returns:
        A tuple of `(media_hash, profile)`.
    """
    profile = options.profile
    out_dir = output_dir(media_hash, profile)
    m3u8_path = out_dir / "index.m3u8"

    if is_complete(m3u8_path):
        logger.debug("HLS already complete: %s", out_dir)
        return media_hash, profile

    # another worker owns the output lock while transcoding this profile
    lock = _acquire_lock(out_dir)
    if lock is None:
        logger.debug("HLS transcode already in progress: %s", out_dir)
        if not await wait_segment(m3u8_path):
            raise RuntimeError("HLS first segment was not ready in time")
        return media_hash, profile

    # another process may have completed after the initial check but before
    # this process acquired the lock
    if is_complete(m3u8_path):
        logger.debug("HLS completed while acquiring the lock: %s", out_dir)
        _release_lock(lock)
        return media_hash, profile

    lock_handed_off = False
    proc: asyncio.subprocess.Process | None = None
    try:
        cleanup_stale_hls(out_dir)

        # reuse one probe for duration tracking and GOP sizing
        duration, fps = await _probe_media(media_path)
        effective_options = (
            replace(options, framerate=fps) if fps is not None else options
        )

        cmd = await _build_hls_cmd(media_path, out_dir, effective_options)
        logger.info("Starting ffmpeg HLS: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        task_id = await register_task(
            media_path, media_hash, effective_options, out_dir, proc, duration
        )

        _start_monitor(proc, lock, task_id)
        lock_handed_off = True

        if not await wait_segment(m3u8_path, proc=proc):
            if proc.returncode is not None:
                raise RuntimeError(
                    "ffmpeg exited before generating the first HLS segment"
                )
            raise RuntimeError("HLS first segment was not ready in time")

    except Exception:
        if not lock_handed_off:
            if proc is not None:
                await _terminate_ffmpeg(proc)
            _release_lock(lock)
        raise

    return media_hash, profile


async def _build_hls_cmd(
    input_path: str, out_dir: Path, options: TranscodeOptions
) -> list[str]:
    """Build the ffmpeg command line for HLS transcoding.

    Combines strategy-specific decoding, encoding, filtering, and keyframe
    arguments with shared audio, timestamp, and HLS output settings.

    The command structure, argument ordering, and per-encoder parameters are
    referenced from Jellyfin: https://github.com/jellyfin/jellyfin

    Args:
        input_path: The source media file path.
        out_dir: The output directory for M3U8 playlist and TS segments.
        options: The transcode parameters (encoder, quality, resolution).

    Returns:
        A list of command-line arguments ready for `asyncio.create_subprocess_exec`.
    """
    cmd = [await _ffmpeg(), "-hide_banner", "-loglevel", "error"]

    seg_len = 6

    # software scale cannot consume GPU-resident frames
    # omit hwaccel_output_format so decoding stays in system memory
    needs_scale = options.max_height is not None

    from app.core.transcode.hwaccels import get_hwaccel

    hwaccel = get_hwaccel(options.hwaccel)
    cmd.extend(await hwaccel.input_args(needs_scale))

    cmd.extend(["-i", input_path])

    # strip metadata and chapters that web playback does not use
    cmd.extend(["-map_metadata", "-1", "-map_chapters", "-1"])

    cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])

    vf_parts: list[str] = []
    if needs_scale:
        # preserve aspect ratio with a 16-pixel-aligned width and even height
        target_height = f"trunc(min({options.max_height},ih)/2)*2"
        vf_parts.append(
            f"scale='max(trunc(iw*{target_height}/ih/16)*16,16)':'{target_height}'"
        )

    vf_parts.extend(hwaccel.video_filters(needs_scale))

    enc = options.encoder
    cmd.extend(["-c:v", enc])
    cmd.extend(hwaccel.encoder_args(options))

    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])

    cmd.extend(hwaccel.keyframe_args(options, seg_len))

    cmd.extend(
        [
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-ar",
            "48000",
        ]
    )

    cmd.extend(["-copyts", "-avoid_negative_ts", "disabled"])

    m3u8_path = str(out_dir / "index.m3u8")
    segment_pattern = str(out_dir / "segment_%06d.ts")
    cmd.extend(
        [
            "-f",
            "hls",
            "-hls_time",
            str(seg_len),
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_segment_type",
            "mpegts",
            "-hls_segment_filename",
            segment_pattern,
            "-hls_flags",
            "append_list",
            "-start_number",
            "0",
            "-max_delay",
            "5000000",
            "-max_muxing_queue_size",
            "128",
            m3u8_path,
        ]
    )

    cmd.extend(["-y", "-nostdin"])
    return cmd


def _acquire_lock(out_dir: Path) -> FileLock | None:
    """Try to acquire an exclusive transcode lock for the given output directory.

    Uses a non-blocking `FileLock` on a `.lock` file within the output directory.

    Args:
        out_dir: The output directory to lock.

    Returns:
        The acquired `FileLock` instance if successful,
        or `None` if another process holds the lock.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(out_dir / ".lock", blocking=False)
    try:
        lock.acquire()
        return lock
    except Timeout:
        return None


def _release_lock(lock: FileLock):
    """Release the transcode lock, suppressing any exceptions.

    Args:
        lock: The `FileLock` instance to release.
    """
    with contextlib.suppress(Exception):
        lock.release()


async def _terminate_ffmpeg(proc: asyncio.subprocess.Process) -> None:
    """Stop an unmonitored ffmpeg process without masking setup errors.

    Sends a graceful termination request first, then kills the process if it
    does not exit before the configured timeout. Cleanup failures are logged
    instead of replacing the original setup exception.

    Args:
        proc: The ffmpeg subprocess that failed before monitor handoff.
    """
    try:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
        try:
            await asyncio.wait_for(
                proc.communicate(), timeout=_PROCESS_TERMINATE_TIMEOUT
            )
        except TimeoutError:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            await proc.communicate()
    except Exception:
        logger.warning(
            "Failed to stop unmonitored ffmpeg process %s",
            getattr(proc, "pid", None),
            exc_info=True,
        )


async def _monitor_ffmpeg(
    proc: asyncio.subprocess.Process, lock: FileLock, task_id: str | None = None
):
    """Monitor ffmpeg completion, update task state, and release the lock.

    Reads stderr and retains a bounded tail for failed tasks. Cancellation
    stops the subprocess, records the task as stopped, and then propagates
    cancellation.

    Args:
        proc: The ffmpeg subprocess to monitor.
        lock: The `FileLock` instance.
        task_id: The registered task ID, if task tracking is enabled.
    """
    try:
        stderr_data = b""
        try:
            if proc.stderr is not None:
                stderr_data = await proc.stderr.read()
        except Exception:
            # still reap and classify the process when stderr capture fails
            pass
        await proc.wait()

        error_tail = None
        # treat exit 255 as an application-requested stop
        if proc.returncode not in (0, 255):
            if stderr_data:
                with contextlib.suppress(Exception):
                    error_tail = stderr_data.decode(errors="replace")[-500:]
            logger.error(
                "ffmpeg HLS exited with code %d for '%s': %s",
                proc.returncode,
                Path(lock.lock_file).parent,
                error_tail or "",
            )

        if task_id:
            await finish_task(task_id, proc.returncode, error_tail)
    except asyncio.CancelledError:
        await _terminate_ffmpeg(proc)
        if task_id:
            try:
                await finish_task(task_id, 255)
            except Exception:
                logger.error(
                    "Failed to stop cancelled transcode task: %s",
                    task_id,
                    exc_info=True,
                )
        raise
    finally:
        _release_lock(lock)
    logger.debug("ffmpeg HLS finished for '%s'", Path(lock.lock_file).parent)


def _start_monitor(
    proc: asyncio.subprocess.Process, lock: FileLock, task_id: str | None = None
) -> asyncio.Task[None]:
    """Start and retain an ffmpeg monitor task until it finishes.

    Args:
        proc: The ffmpeg subprocess to monitor.
        lock: The `FileLock` held for the output directory.
        task_id: The registered transcode task ID, if available.

    Returns:
        The scheduled monitor task.
    """

    def _done(task: asyncio.Task[None]) -> None:
        """Remove a completed monitor and consume its exception."""
        _MONITOR_TASKS.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.error(
                "Transcode monitor task failed: %s",
                task.get_name(),
                exc_info=True,
            )

    name = f"transcode-monitor:{task_id or proc.pid}"
    task = asyncio.create_task(_monitor_ffmpeg(proc, lock, task_id), name=name)
    _MONITOR_TASKS.add(task)
    task.add_done_callback(_done)
    return task


async def shutdown_monitors() -> None:
    """Cancel and wait for all ffmpeg monitor tasks in this worker."""
    monitors = tuple(_MONITOR_TASKS)
    if not monitors:
        return
    for task in monitors:
        task.cancel()
    await asyncio.gather(*monitors, return_exceptions=True)
    _MONITOR_TASKS.difference_update(monitors)
