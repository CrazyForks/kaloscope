import asyncio
import contextlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_CAPABILITY_TIMEOUT = 10.0
_RUNTIME_PROBE_TIMEOUT = 10.0
_RUNTIME_REAP_TIMEOUT = 1.0
_RUNTIME_STDERR_LIMIT = 2048
_LISTING_OPTIONS = {
    "encoders": "-encoders",
    "filters": "-filters",
    "hwaccels": "-hwaccels",
    "bsfs": "-bsfs",
    "muxers": "-muxers",
}

_CAPABILITY_CACHE: dict[tuple[str, str], "FFmpegCapabilities"] = {}
_HARDWARE_ENCODER_CACHE: set[tuple[str, str, str | None]] = set()
_HARDWARE_DECODE_CACHE: set[tuple[str, str, str | None, str, int, int, int]] = set()
_HARDWARE_TRANSFORM_CACHE: set[tuple[str, str, str | None, str, int, int, int, str]] = (
    set()
)


@dataclass(frozen=True)
class FFmpegCapabilities:
    """Capabilities advertised by one FFmpeg executable and video encoder."""

    executable: str
    encoders: frozenset[str]
    filters: frozenset[str]
    hwaccels: frozenset[str]
    bsfs: frozenset[str]
    muxers: frozenset[str]
    encoder_options: frozenset[str]

    def supports_encoder(self, name: str) -> bool:
        return name in self.encoders

    def supports_filter(self, name: str) -> bool:
        return name in self.filters

    def supports_hwaccel(self, name: str) -> bool:
        return name in self.hwaccels

    def supports_bsf(self, name: str) -> bool:
        return name in self.bsfs

    def supports_muxer(self, name: str) -> bool:
        return name in self.muxers

    def supports_encoder_option(self, name: str) -> bool:
        return name in self.encoder_options


def _parse_listing(kind: str, output: str) -> set[str]:
    """Parse names from one FFmpeg capability-listing command."""
    names: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if kind == "encoders":
            if (
                len(parts) >= 2
                and len(parts[0]) == 6
                and parts[0][0] in "VAS"
                and set(parts[0][1:]) <= set(".FSXBD")
            ):
                names.add(parts[1])
        elif kind == "filters":
            match = re.match(r"^\s*[TSC.]{2,3}\s+(\w+)\s+", line)
            if match:
                names.add(match.group(1))
        elif kind == "muxers":
            if len(parts) >= 2 and "E" in parts[0] and set(parts[0]) <= set("DEd."):
                names.update(parts[1].split(","))
        elif kind in {"hwaccels", "bsfs"}:
            value = line.strip()
            if value and " " not in value and re.fullmatch(r"[\w,-]+", value):
                names.update(value.split(","))
        else:
            raise ValueError(f"Unsupported FFmpeg capability kind: {kind}")
    return names


def _parse_encoder_options(output: str) -> set[str]:
    """Parse private AVOption names from FFmpeg encoder help."""
    return set(re.findall(r"^\s+-([^\s]+)\s+", output, flags=re.MULTILINE))


async def _query_ffmpeg(executable: str, *args: str) -> str:
    """Run one bounded FFmpeg information query and return combined output."""
    proc = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CAPABILITY_TIMEOUT
        )
    except TimeoutError as exc:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.communicate()
        raise RuntimeError(
            f"FFmpeg capability discovery timed out for '{executable}'"
        ) from exc
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"FFmpeg capability discovery failed for '{executable}': "
            f"{detail or f'exit code {proc.returncode}'}"
        )
    return (stdout + stderr).decode(errors="replace")


async def _read_stderr_tail(stream: asyncio.StreamReader) -> bytes:
    """Drain stderr while retaining only a bounded byte tail."""
    tail = bytearray()
    while chunk := await stream.read(4096):
        tail.extend(chunk)
        overflow = len(tail) - _RUNTIME_STDERR_LIMIT
        if overflow > 0:
            del tail[:overflow]
    return bytes(tail)


async def _kill_and_reap_probe(proc: asyncio.subprocess.Process) -> None:
    """Best-effort terminate and reap one probe within a fixed bound."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=_RUNTIME_REAP_TIMEOUT)


async def _run_ffmpeg_probe(executable: str, args: list[str]) -> tuple[bool, str]:
    """Run one bounded FFmpeg runtime probe without creating output files."""
    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return False, str(exc)
    assert proc.stderr is not None
    try:
        _, stderr = await asyncio.wait_for(
            asyncio.gather(proc.wait(), _read_stderr_tail(proc.stderr)),
            timeout=_RUNTIME_PROBE_TIMEOUT,
        )
    except TimeoutError:
        await _kill_and_reap_probe(proc)
        return False, f"timed out after {_RUNTIME_PROBE_TIMEOUT:.1f} seconds"
    except BaseException:
        await _kill_and_reap_probe(proc)
        raise
    detail = stderr.decode(errors="replace").strip()
    return proc.returncode == 0, detail


def _resolved_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    if resolved:
        return str(Path(resolved).resolve())
    return executable


async def require_hardware_encoder(
    executable: str,
    strategy: str,
    encoder: str,
    device: str | None,
    args: list[str],
) -> None:
    """Require one hardware encoder/device combination to encode a frame."""
    executable = _resolved_executable(executable)
    key = (executable, strategy, device)
    if key in _HARDWARE_ENCODER_CACHE:
        return
    success, detail = await _run_ffmpeg_probe(executable, args)
    if not success:
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"Hardware encoder '{encoder}' is unavailable for {strategy} "
            f"on device '{device or 'default'}'{suffix}"
        )
    _HARDWARE_ENCODER_CACHE.add(key)


def _decode_cache_key(
    executable: str,
    strategy: str,
    device: str | None,
    media_path: str,
    stream_index: int,
) -> tuple[str, str, str | None, str, int, int, int] | None:
    path = Path(media_path).resolve()
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        executable,
        strategy,
        device,
        str(path),
        stat.st_size,
        stat.st_mtime_ns,
        stream_index,
    )


async def probe_hardware_decode(
    executable: str,
    strategy: str,
    device: str | None,
    media_path: str,
    stream_index: int,
    args: list[str],
) -> bool:
    """Probe source hardware decoding and cache successful file states."""
    executable = _resolved_executable(executable)
    key = _decode_cache_key(
        executable,
        strategy,
        device,
        media_path,
        stream_index,
    )
    if key is not None and key in _HARDWARE_DECODE_CACHE:
        return True
    success, _ = await _run_ffmpeg_probe(executable, args)
    if success and key is not None:
        _HARDWARE_DECODE_CACHE.add(key)
    return success


async def probe_hardware_transform(
    executable: str,
    strategy: str,
    device: str | None,
    media_path: str,
    stream_index: int,
    signature: str,
    args: list[str],
) -> bool:
    """Probe a source hardware-filter graph and cache successful file states."""
    executable = _resolved_executable(executable)
    decode_key = _decode_cache_key(
        executable,
        strategy,
        device,
        media_path,
        stream_index,
    )
    key = (*decode_key, signature) if decode_key is not None else None
    if key is not None and key in _HARDWARE_TRANSFORM_CACHE:
        return True
    success, _ = await _run_ffmpeg_probe(executable, args)
    if success and key is not None:
        _HARDWARE_TRANSFORM_CACHE.add(key)
    return success


async def load_ffmpeg_capabilities(executable: str, encoder: str) -> FFmpegCapabilities:
    """Discover and cache FFmpeg capabilities for the selected video encoder."""
    executable = _resolved_executable(executable)
    key = (executable, encoder)
    cached = _CAPABILITY_CACHE.get(key)
    if cached is not None:
        return cached

    kinds = tuple(_LISTING_OPTIONS)
    outputs = await asyncio.gather(
        *(_query_ffmpeg(executable, _LISTING_OPTIONS[kind]) for kind in kinds)
    )
    listings = {
        kind: frozenset(_parse_listing(kind, output))
        for kind, output in zip(kinds, outputs, strict=True)
    }

    encoder_options: frozenset[str] = frozenset()
    if encoder in listings["encoders"]:
        help_output = await _query_ffmpeg(executable, "-h", f"encoder={encoder}")
        encoder_options = frozenset(_parse_encoder_options(help_output))

    capabilities = FFmpegCapabilities(
        executable=executable,
        encoders=listings["encoders"],
        filters=listings["filters"],
        hwaccels=listings["hwaccels"],
        bsfs=listings["bsfs"],
        muxers=listings["muxers"],
        encoder_options=encoder_options,
    )
    _CAPABILITY_CACHE[key] = capabilities
    return capabilities


def clear_ffmpeg_capability_cache() -> None:
    """Clear cached capability snapshots, primarily for configuration changes."""
    _CAPABILITY_CACHE.clear()
    _HARDWARE_ENCODER_CACHE.clear()
    _HARDWARE_DECODE_CACHE.clear()
    _HARDWARE_TRANSFORM_CACHE.clear()
