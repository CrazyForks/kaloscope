import asyncio
import contextlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_CAPABILITY_TIMEOUT = 10.0
_LISTING_OPTIONS = {
    "encoders": "-encoders",
    "filters": "-filters",
    "hwaccels": "-hwaccels",
    "bsfs": "-bsfs",
    "muxers": "-muxers",
}

_CAPABILITY_CACHE: dict[tuple[str, str], "FFmpegCapabilities"] = {}


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


def _resolved_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    if resolved:
        return str(Path(resolved).resolve())
    return executable


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
