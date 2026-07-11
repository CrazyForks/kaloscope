import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from app.core.transcode.options import EncoderConfig, TranscodeOptions


@dataclass
class TranscodeContext:
    """Runtime context used to build a transcode command."""

    options: TranscodeOptions
    source_framerate: float = 30.0
    segment_length: ClassVar[int] = 6

    @property
    def needs_scale(self) -> bool:
        return self.options.max_height is not None

    @property
    def encoder_config(self) -> EncoderConfig:
        return self.options.encoder_config


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
        if config.hwaccel:
            cmd.extend(["-hwaccel", config.hwaccel])
            if config.hwaccel_output_format and not context.needs_scale:
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
        gop = math.ceil(context.source_framerate * context.segment_length)
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{context.segment_length})",
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
