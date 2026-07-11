from app.core.transcode.hwaccels.base import HWAccelStrategy, TranscodeContext


class VideoToolbox(HWAccelStrategy):
    """Apple VideoToolbox H.264 encoding strategy."""

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Convert original-resolution 10-bit hardware frames to NV12."""
        if not context.needs_scale and context.source_is_10_bit:
            return ["scale_vt=format=nv12"]
        return []

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build VideoToolbox bitrate and speed-priority options.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for bitrate-controlled VideoToolbox encoding.
        """
        quality = context.options.quality
        bitrate = context.options.bitrate
        vt_prio = "0" if quality in ("high", "medium") else "1"
        return [
            "-b:v",
            bitrate,
            # disable quantizer bounds so bitrate control acts alone
            "-qmin",
            "-1",
            "-qmax",
            "-1",
            "-prio_speed",
            vt_prio,
        ]
