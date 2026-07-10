from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class VideoToolbox(HWAccelStrategy):
    config = ENCODER_CONFIG["videotoolbox"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        bitrate = HW_BITRATE.get(options.quality, "3000k")
        vt_prio = "0" if options.quality in ("high", "medium") else "1"
        return [
            "-b:v",
            bitrate,
            # qmin=-1 / qmax=-1 disable quantization constraints,
            # letting the encoder use pure bitrate-based rate control
            "-qmin",
            "-1",
            "-qmax",
            "-1",
            "-prio_speed",
            vt_prio,
        ]
