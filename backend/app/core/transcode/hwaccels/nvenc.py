import math

from app.core.transcode.hwaccels.base import HWAccelStrategy, TranscodeContext


class NVENC(HWAccelStrategy):
    """NVIDIA NVENC H.264 encoding strategy."""

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build NVENC preset and constrained bitrate options.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for the selected quality preset and bitrate.
        """
        options = context.options
        bitrate = options.bitrate
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

    def keyframe_args(self, context: TranscodeContext) -> list[str]:
        """Build a fixed GOP approximating one HLS segment.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for fixed GOP and minimum keyframe intervals.
        """
        gop = math.ceil(context.source_framerate * context.segment_length)
        return [
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
