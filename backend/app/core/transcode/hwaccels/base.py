import math
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.transcode.options import EncoderConfig, TranscodeOptions


async def resolve_vaapi_device() -> str | None:
    """Get the VAAPI render device path.

    Checks the `vaapi.device` global config first, falls back to the
    standard render node `/dev/dri/renderD128`.

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
    config: EncoderConfig

    async def input_args(self, needs_scale: bool) -> list[str]:
        """Build FFmpeg input options for hardware-accelerated decoding.

        The returned arguments are inserted before the input file. When CPU
        scaling is required, the configured hardware output format is omitted
        so decoded frames remain accessible to software filters. This method is
        asynchronous to allow implementations to discover or initialize a
        hardware device before building the command.

        Args:
            needs_scale: Whether the transcode uses a software scaling filter.

        Returns:
            FFmpeg command-line arguments to place before the input file.
        """
        cmd: list[str] = []
        if self.config.hwaccel:
            cmd.extend(["-hwaccel", self.config.hwaccel])
            if self.config.hwaccel_output_format and not needs_scale:
                cmd.extend(
                    ["-hwaccel_output_format", self.config.hwaccel_output_format]
                )
        return cmd

    def video_filters(self, needs_scale: bool) -> list[str]:
        """Build strategy-specific FFmpeg video filter expressions.

        The expressions are appended after any software scaling filter and are
        joined by the caller to form the value passed to FFmpeg's `-vf`
        option. The default strategy requires no additional filters.

        Args:
            needs_scale: Whether the transcode uses a software scaling filter.

        Returns:
            Video filter expressions in the order FFmpeg should apply them.
        """
        return []

    @abstractmethod
    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        """Build FFmpeg options for the strategy's video encoder.

        The returned arguments are inserted immediately after the video codec
        selection. Implementations use the requested quality and other
        transcode settings to configure encoder-specific rate control and
        output parameters.

        Args:
            options: Requested quality, resolution, frame rate, and accelerator.

        Returns:
            FFmpeg command-line arguments for configuring the video encoder.

        Raises:
            NotImplementedError: If a concrete strategy does not implement the
                method.
        """
        raise NotImplementedError

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        """Build FFmpeg options that align keyframes with HLS segments.

        The default strategy combines timestamp-based forced keyframes with a
        fixed GOP size. The GOP length is the segment duration multiplied by
        the source frame rate and rounded up to a whole frame.

        Args:
            options: Transcode settings containing the source frame rate.
            seg_len: Target HLS segment duration in seconds.

        Returns:
            FFmpeg command-line arguments for keyframe and GOP configuration.
        """
        # unknown encoder: apply both strategies for safety
        gop = math.ceil(options.framerate * seg_len)
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{seg_len})",
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
