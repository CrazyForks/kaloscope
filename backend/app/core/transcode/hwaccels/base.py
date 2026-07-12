import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from app.core.transcode.capabilities import FFmpegCapabilities
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
        if config.hwaccel and context.supports_hwaccel(config.hwaccel):
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
