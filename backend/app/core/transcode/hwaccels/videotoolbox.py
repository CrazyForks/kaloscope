from app.core.transcode.hwaccels.base import HWAccelStrategy, TranscodeContext


class VideoToolbox(HWAccelStrategy):
    """Apple VideoToolbox H.264 encoding strategy."""

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Normalize non-YUV420P VideoToolbox frames to NV12."""
        pixel_format = context.source_pixel_format
        if not context.needs_scale and (
            pixel_format is None or pixel_format.lower() != "yuv420p"
        ):
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
