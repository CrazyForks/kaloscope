import math

from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class NVENC(HWAccelStrategy):
    """NVIDIA NVENC H.264 encoding strategy."""

    config = ENCODER_CONFIG["nvenc"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        """Build NVENC preset and constrained bitrate options.

        Args:
            options: The requested transcode settings.

        Returns:
            FFmpeg options for the selected quality preset and bitrate.
        """
        bitrate = HW_BITRATE.get(options.quality, "3000k")
        nvenc_preset = (
            "p4"
            if options.quality == "medium"
            else ("p7" if options.quality == "high" else "p1")
        )
        return [
            "-preset",
            nvenc_preset,
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            str(int(bitrate[:-1]) * 2) + "k",
        ]

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        """Build a fixed GOP approximating one HLS segment.

        Args:
            options: Transcode settings containing the source frame rate.
            seg_len: The target HLS segment duration in seconds.

        Returns:
            FFmpeg options for fixed GOP and minimum keyframe intervals.
        """
        gop = math.ceil(options.framerate * seg_len)
        return [
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
