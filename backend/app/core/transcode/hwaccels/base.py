import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from sanic.log import logger

from app.core.transcode.capabilities import (
    FFmpegCapabilities,
    probe_hardware_decode,
    require_hardware_encoder,
)
from app.core.transcode.options import EncoderConfig, TranscodeOptions


class HDRType(StrEnum):
    """HDR subtype inferred from explicit media metadata."""

    SDR = "sdr"
    HDR10 = "hdr10"
    HLG = "hlg"
    HDR10_PLUS = "hdr10_plus"
    DOVI_COMPATIBLE = "dovi_compatible"
    DOVI_ONLY = "dovi_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MediaProbe:
    """Selected stream indexes and metadata probed from a media container."""

    video_stream_index: int | None = None
    audio_stream_index: int | None = None
    height: int | None = None
    duration: float | None = None
    framerate: float | None = None
    codec: str | None = None
    profile: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    color_range: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    hdr10_plus: bool = False
    dovi_profile: int | None = None
    dovi_bl_present: bool | None = None
    dovi_bl_signal_compatibility_id: int | None = None


@dataclass(frozen=True)
class HardwareRuntime:
    """Prepared hardware device and source decode decision."""

    device: str | None
    can_decode_source: bool


def _normalized_profile(value: str) -> str:
    return value.lower().replace(" ", "").replace("_", "").replace("-", "")


def is_hardware_decode_candidate(metadata: MediaProbe) -> bool:
    """Return whether source metadata is safe to verify for hardware decode."""
    codec = (metadata.codec or "").lower()
    profile = metadata.profile
    pixel_format = (metadata.pixel_format or "").lower()
    bit_depth = metadata.bit_depth
    if not profile or bit_depth is None or not pixel_format:
        return False
    normalized_profile = _normalized_profile(profile)
    if codec in {"h264", "avc"}:
        return (
            normalized_profile
            in {
                "constrainedbaseline",
                "baseline",
                "extended",
                "main",
                "high",
                "progressivehigh",
                "constrainedhigh",
            }
            and bit_depth == 8
            and pixel_format in {"yuv420p", "yuvj420p", "nv12"}
        )
    if codec in {"hevc", "h265"}:
        if normalized_profile == "main":
            return bit_depth == 8 and pixel_format in {"yuv420p", "nv12"}
        if normalized_profile == "main10":
            return bit_depth == 10 and pixel_format in {"yuv420p10le", "p010le"}
    return False


def classify_hdr(metadata: MediaProbe) -> HDRType:
    """Classify HDR metadata without guessing from incomplete HDR signals."""
    transfer = (metadata.color_transfer or "").lower()
    is_pq = transfer == "smpte2084"
    is_hlg = transfer == "arib-std-b67"
    primaries = (metadata.color_primaries or "").lower()
    color_space = (metadata.color_space or "").lower()
    has_valid_hdr_color = (
        metadata.bit_depth is not None
        and metadata.bit_depth >= 10
        and primaries == "bt2020"
        and color_space in {"bt2020nc", "bt2020_ncl", "bt2020c", "bt2020_cl"}
    )

    if metadata.dovi_profile is not None:
        has_compatible_base_layer = metadata.dovi_bl_present is True and (
            metadata.dovi_bl_signal_compatibility_id == 1
            or (
                metadata.dovi_bl_signal_compatibility_id == 6
                and is_pq
                and has_valid_hdr_color
            )
        )
        if has_compatible_base_layer:
            return HDRType.DOVI_COMPATIBLE
        return HDRType.DOVI_ONLY

    has_hdr_signal = is_pq or is_hlg or metadata.hdr10_plus
    if not has_hdr_signal:
        return HDRType.SDR

    if not has_valid_hdr_color:
        return HDRType.UNKNOWN
    if is_pq:
        return HDRType.HDR10_PLUS if metadata.hdr10_plus else HDRType.HDR10
    if is_hlg and not metadata.hdr10_plus:
        return HDRType.HLG
    return HDRType.UNKNOWN


@dataclass
class TranscodeContext:
    """Runtime context used to build a transcode command."""

    options: TranscodeOptions
    metadata: MediaProbe = field(default_factory=MediaProbe)
    capabilities: FFmpegCapabilities | None = None
    hardware: HardwareRuntime | None = None

    @property
    def source_framerate(self) -> float:
        value = self.metadata.framerate
        return value if value is not None and value > 0 else 30.0

    @property
    def source_pixel_format(self) -> str | None:
        return self.metadata.pixel_format

    @property
    def source_height(self) -> int | None:
        return self.metadata.height

    @property
    def needs_scale(self) -> bool:
        max_height = self.options.max_height
        if max_height is None:
            return False
        return self.source_height is None or self.source_height > max_height

    @property
    def scale_height(self) -> str | None:
        max_height = self.options.max_height
        if not self.needs_scale or max_height is None:
            return None
        return f"trunc(min({max_height},ih)/2)*2"

    @property
    def scale_width(self) -> str | None:
        height = self.scale_height
        if height is None:
            return None
        return f"max(trunc(iw*{height}/ih/16)*16,16)"

    @property
    def hdr_type(self) -> HDRType:
        return classify_hdr(self.metadata)

    @property
    def is_hdr10(self) -> bool:
        return self.hdr_type in {
            HDRType.HDR10,
            HDRType.HDR10_PLUS,
            HDRType.DOVI_COMPATIBLE,
        }

    @property
    def is_hlg(self) -> bool:
        return self.hdr_type is HDRType.HLG

    @property
    def needs_tonemap(self) -> bool:
        return self.hdr_type in {
            HDRType.HDR10,
            HDRType.HLG,
            HDRType.HDR10_PLUS,
            HDRType.DOVI_COMPATIBLE,
        }

    @property
    def encoder_config(self) -> EncoderConfig:
        return self.options.encoder_config

    def supports_filter(self, name: str) -> bool:
        """Return whether a filter is available or capabilities are unprobed."""
        return self.capabilities is None or self.capabilities.supports_filter(name)

    def supports_hwaccel(self, name: str) -> bool:
        """Return whether a hardware decoder is available."""
        return self.capabilities is None or self.capabilities.supports_hwaccel(name)

    def supports_encoder_option(self, name: str) -> bool:
        """Return whether the selected encoder advertises a private option."""
        return self.capabilities is None or self.capabilities.supports_encoder_option(
            name
        )

    @property
    def uses_hardware_decode(self) -> bool:
        """Return whether the selected strategy can request hardware decoding."""
        if self.hardware is not None:
            return self.hardware.can_decode_source
        hwaccel = self.encoder_config.hwaccel
        return hwaccel is not None and self.supports_hwaccel(hwaccel)


def software_tonemap_filters(
    context: TranscodeContext, output_format: str
) -> list[str]:
    """Build a standard-FFmpeg CPU HDR-to-SDR filter chain."""
    linear = "zscale=transfer=linear:npl=100"
    width = context.scale_width
    height = context.scale_height
    if width is not None and height is not None:
        linear += f":w='{width}':h='{height}'"
    return [
        linear,
        "format=gbrpf32le",
        "tonemap=hable:desat=0",
        "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv",
        f"format={output_format}",
    ]


async def resolve_vaapi_device() -> str | None:
    """Get the VAAPI render device path.

    Uses the `vaapi.device` global setting when it contains a path; otherwise,
    checks the standard render node `/dev/dri/renderD128`.

    Returns:
        The render device path if it exists, or `None` if not.
    """
    from app.models.general import GlobalConfig

    dev = await GlobalConfig.get_or_none(key="vaapi.device")
    path = (
        dev.value
        if dev and dev.value and isinstance(dev.value, str)
        else "/dev/dri/renderD128"
    )
    return path if Path(path).exists() else None


class HWAccelStrategy(ABC):
    """Base interface for FFmpeg hardware acceleration strategies."""

    async def resolve_hardware_device(self, context: TranscodeContext) -> str | None:
        """Resolve the device used by this hardware strategy."""
        return None

    def encoder_probe_args(
        self, context: TranscodeContext, device: str | None
    ) -> list[str]:
        """Build a one-frame hardware encoder health probe."""
        return [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=1",
            "-frames:v",
            "1",
            "-an",
            "-c:v",
            context.options.encoder,
            "-f",
            "null",
            "-",
        ]

    def allows_hardware_decode(self, context: TranscodeContext) -> bool:
        """Return whether this strategy may probe hardware source decoding."""
        return is_hardware_decode_candidate(context.metadata)

    async def prepare_hardware(
        self, context: TranscodeContext, media_path: str
    ) -> HardwareRuntime | None:
        """Validate hardware encoding and choose decoding for one source."""
        strategy = context.options.hwaccel
        if strategy is None:
            return None
        capabilities = context.capabilities
        encoder = context.options.encoder
        if capabilities is None:
            raise RuntimeError("Hardware preparation requires FFmpeg capabilities")
        if not capabilities.supports_encoder(encoder):
            raise RuntimeError(
                f"FFmpeg '{capabilities.executable}' is missing required "
                f"capabilities: encoders: {encoder}"
            )

        device = await self.resolve_hardware_device(context)
        await require_hardware_encoder(
            capabilities.executable,
            strategy,
            encoder,
            device,
            self.encoder_probe_args(context, device),
        )

        hwaccel = context.encoder_config.hwaccel
        if (
            hwaccel is None
            or not context.supports_hwaccel(hwaccel)
            or not self.allows_hardware_decode(context)
        ):
            return HardwareRuntime(device, False)

        stream_index = context.metadata.video_stream_index
        assert stream_index is not None
        probe_context = replace(
            context,
            hardware=HardwareRuntime(device, True),
        )
        input_args = await self.input_args(probe_context)
        output_format = context.encoder_config.hwaccel_output_format
        if output_format and "-hwaccel_output_format" not in input_args:
            input_args.extend(["-hwaccel_output_format", output_format])
        download_format = "p010le" if context.metadata.bit_depth == 10 else "nv12"
        decode_args = [
            *input_args,
            "-i",
            media_path,
            "-map",
            f"0:{stream_index}",
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"hwdownload,format={download_format}",
            "-f",
            "null",
            "-",
        ]
        can_decode = await probe_hardware_decode(
            capabilities.executable,
            strategy,
            device,
            media_path,
            stream_index,
            decode_args,
        )
        if not can_decode:
            logger.warning(
                "Hardware decode probe failed for %s codec=%s profile=%s "
                "bit_depth=%s; using software decode",
                strategy,
                context.metadata.codec,
                context.metadata.profile,
                context.metadata.bit_depth,
            )
        return HardwareRuntime(device, can_decode)

    def keep_hardware_frames(self, context: TranscodeContext) -> bool:
        """Return whether decoding should keep frames in device memory.

        Args:
            context: The runtime transcode context.

        Returns:
            Whether to keep hardware frames.
        """
        return not context.needs_scale

    async def input_args(self, context: TranscodeContext) -> list[str]:
        """Build FFmpeg input options for hardware-accelerated decoding.

        The returned arguments are inserted before the input file. When CPU
        scaling is required, the configured hardware output format is omitted
        so decoded frames remain accessible to software filters. This method is
        asynchronous to allow implementations to discover or initialize a
        hardware device before building the command.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg command-line arguments to place before the input file.
        """
        cmd: list[str] = []
        config = context.encoder_config
        if config.hwaccel and context.uses_hardware_decode:
            cmd.extend(["-hwaccel", config.hwaccel])
            if config.hwaccel_output_format and self.keep_hardware_frames(context):
                cmd.extend(["-hwaccel_output_format", config.hwaccel_output_format])
        return cmd

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Build strategy-specific FFmpeg video filter expressions.

        The expressions are appended after any software scaling filter and are
        joined by the caller to form the value passed to FFmpeg's `-vf`
        option. The default strategy requires no additional filters.

        Args:
            context: The runtime transcode context.

        Returns:
            Video filter expressions in the order FFmpeg should apply them.
        """
        return []

    @abstractmethod
    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build FFmpeg options for the strategy's video encoder.

        The returned arguments are inserted immediately after the video codec
        selection. Implementations use the requested quality and other
        transcode settings to configure encoder-specific rate control and
        output parameters.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg command-line arguments for configuring the video encoder.

        Raises:
            NotImplementedError: If a concrete strategy does not implement the method.
        """
        raise NotImplementedError

    def keyframe_args(self, context: TranscodeContext) -> list[str]:
        """Build FFmpeg options that place keyframes near HLS segment boundaries.

        The default strategy combines timestamp-based forced keyframes with a
        fixed GOP size. For constant-frame-rate input, the GOP length
        approximates one segment duration after rounding up to a whole frame.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg command-line arguments for keyframe and GOP configuration.
        """
        segment_length = context.options.segment_length
        gop = math.ceil(context.source_framerate * segment_length)
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{segment_length})",
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
