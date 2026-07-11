from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class VideoToolbox(HWAccelStrategy):
    """Apple VideoToolbox H.264 encoding strategy."""

    config = ENCODER_CONFIG["videotoolbox"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        """Build VideoToolbox bitrate and speed-priority options.

        Args:
            options: The requested transcode settings.

        Returns:
            FFmpeg options for bitrate-controlled VideoToolbox encoding.
        """
        bitrate = HW_BITRATE.get(options.quality, "3000k")
        vt_prio = "0" if options.quality in ("high", "medium") else "1"
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
