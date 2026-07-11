from app.core.transcode.hwaccels.base import HWAccelStrategy, TranscodeContext
from app.core.transcode.options import HW_BITRATE


class VideoToolbox(HWAccelStrategy):
    """Apple VideoToolbox H.264 encoding strategy."""

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build VideoToolbox bitrate and speed-priority options.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for bitrate-controlled VideoToolbox encoding.
        """
        quality = context.options.quality
        bitrate = HW_BITRATE.get(quality, "3000k")
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
