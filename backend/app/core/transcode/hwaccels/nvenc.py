import math

from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class NVENC(HWAccelStrategy):
    config = ENCODER_CONFIG["nvenc"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
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
        # Approximate one segment per GOP for constant-frame-rate input.
        gop = math.ceil(options.framerate * seg_len)
        return [
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
