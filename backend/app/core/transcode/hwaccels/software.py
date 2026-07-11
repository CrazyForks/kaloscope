from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class Software(HWAccelStrategy):
    config = ENCODER_CONFIG[None]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        bitrate = HW_BITRATE.get(options.quality, "3000k")
        bitrate_num = int(bitrate[:-1])
        bufsize = str(bitrate_num * 2) + "k"
        return [
            "-preset",
            "veryfast",
            "-crf",
            str(options.crf),
            "-profile:v",
            "main",
            "-level",
            "4.0",
            "-pix_fmt",
            "yuv420p",
            # VBV constraints cap peak bitrate during CRF encoding,
            # preventing network-unfriendly bitrate spikes
            "-maxrate",
            bitrate,
            "-bufsize",
            bufsize,
        ]

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{seg_len})",
            # prevent extra scene-change keyframes and keep the GOP structure
            # deterministic
            "-sc_threshold:v:0",
            "0",
        ]
