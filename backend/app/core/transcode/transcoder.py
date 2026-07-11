import asyncio
import contextlib
from pathlib import Path

from filelock import FileLock, Timeout
from sanic.log import logger

from app.core.transcode.hls import (
    _cleanup_stale_hls,
    _is_complete,
    _wait_segment,
    output_dir,
)
from app.core.transcode.options import TranscodeOptions
from app.core.transcode.tasks import finish_task, register_task

_PROCESS_TERMINATE_TIMEOUT = 5.0


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
    """Get the ffprobe executable path from global config or default to "ffprobe".

    Returns:
        The ffprobe executable name or path.
    """
    ffmpeg = await _ffmpeg()
    if ffmpeg != "ffmpeg":
        path = Path(ffmpeg).with_name("ffprobe")
        if path.is_file():
            return str(path)
    return "ffprobe"


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
    proc = await asyncio.create_subprocess_exec(
        await _ffprobe(),
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        media_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    try:
        return float(stdout.decode().strip())
    except (ValueError, TypeError):
        return None


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
    proc = await asyncio.create_subprocess_exec(
        await _ffprobe(),
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        media_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    raw = stdout.decode().strip()
    try:
        num, _, den = raw.partition("/")
        fps = float(num) / float(den) if den else float(num)
    except (ValueError, TypeError, ZeroDivisionError):
        return None
    # guard against bogus values (e.g. "0/0" -> 0.0)
    return fps if fps > 0 else None


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

    # return immediately if the M3U8 already exists and is complete
    if _is_complete(m3u8_path):
        logger.debug("HLS already complete: %s", out_dir)
        return media_hash, profile

    # if another ffmpeg is running for this directory, just wait
    lock = _acquire_lock(out_dir)
    if lock is None:
        logger.debug("HLS transcode already in progress: %s", out_dir)
        if not await _wait_segment(m3u8_path):
            raise RuntimeError("HLS first segment was not ready in time")
        return media_hash, profile

    # another process may have completed after the initial check but before
    # this process acquired the lock
    if _is_complete(m3u8_path):
        logger.debug("HLS completed while acquiring the lock: %s", out_dir)
        _release_lock(lock)
        return media_hash, profile

    # start the ffmpeg process if we acquired the lock
    lock_handed_off = False
    proc: asyncio.subprocess.Process | None = None
    try:
        _cleanup_stale_hls(out_dir)

        # Probe the average source framerate so GOP-based hardware encoders can
        # approximate one GOP per HLS segment for constant-frame-rate input.
        fps = await probe_framerate(media_path)
        if fps is not None:
            options.framerate = fps

        cmd = await _build_hls_cmd(media_path, out_dir, options)
        logger.info("Starting ffmpeg HLS: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # register the task in the memory store
        duration = await probe_duration(media_path)
        task_id = await register_task(
            media_path, media_hash, options, out_dir, proc, duration
        )

        # monitor completion in the background
        asyncio.ensure_future(_monitor_ffmpeg(proc, lock, task_id))
        lock_handed_off = True

        # wait for at least one segment so the player can start immediately
        if not await _wait_segment(m3u8_path, proc=proc):
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


async def _build_hls_cmd(
    input_path: str, out_dir: Path, options: TranscodeOptions
) -> list[str]:
    """Build the ffmpeg command line for HLS transcoding.

    Constructs a complete ffmpeg command that transcodes a source video into
    an HLS playlist with MPEG-TS segments.  The command configures hardware
    acceleration (if requested), video codec parameters (CRF for libx264,
    bitrate- or QP-based for hardware encoders), audio encoding (AAC 128k
    stereo), keyframe placement near segment boundaries, and HLS output settings.

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

    # HLS segment length in seconds
    seg_len = 6

    # when scaling is requested we use a CPU `scale` filter, which cannot
    # operate on GPU-resident frames; in that case skip hwaccel_output_format
    # so decoded frames stay in system memory (the encoder re-uploads them)
    needs_scale = options.max_height is not None

    # hardware acceleration
    from app.core.transcode.hwaccels import get_hwaccel

    hwaccel = get_hwaccel(options.hwaccel)
    cmd.extend(await hwaccel.input_args(needs_scale))

    cmd.extend(["-i", input_path])

    # strip metadata and chapters from output (not needed for web playback)
    cmd.extend(["-map_metadata", "-1", "-map_chapters", "-1"])

    # stream mapping placed before codec arguments
    cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])

    # video filter chain
    vf_parts: list[str] = []
    if needs_scale:
        # scale filter to limit the output height while preserving aspect ratio,
        # and ensure the dimensions are compatible with H.264 encoders
        target_height = f"trunc(min({options.max_height},ih)/2)*2"
        vf_parts.append(
            f"scale='max(trunc(iw*{target_height}/ih/16)*16,16)':'{target_height}'"
        )

    vf_parts.extend(hwaccel.video_filters(needs_scale))

    # video encoder and parameters
    enc = options.encoder
    cmd.extend(["-c:v", enc])
    cmd.extend(hwaccel.encoder_args(options))

    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])

    # -------------------- Keyframe / GOP --------------------

    cmd.extend(hwaccel.keyframe_args(options, seg_len))

    # -------------------- Audio --------------------
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

    # preserve original timestamps and disable negative timestamp avoidance
    cmd.extend(["-copyts", "-avoid_negative_ts", "disabled"])

    # -------------------- HLS output --------------------
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


async def _monitor_ffmpeg(
    proc: asyncio.subprocess.Process, lock: FileLock, task_id: str | None = None
):
    """Wait for ffmpeg to finish, log errors, and release the lock.

    Args:
        proc: The ffmpeg subprocess to monitor.
        lock: The `FileLock` instance.
        task_id: The registered task ID, if task tracking is enabled.
    """
    stderr_data = b""
    try:
        if proc.stderr is not None:
            stderr_data = await proc.stderr.read()
    except Exception:
        pass
    await proc.wait()

    try:
        error_tail = None
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
    finally:
        _release_lock(lock)
    logger.debug("ffmpeg HLS finished for '%s'", Path(lock.lock_file).parent)
