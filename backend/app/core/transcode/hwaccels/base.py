import math
from abc import ABC, abstractmethod

from app.core.transcode.options import EncoderConfig, TranscodeOptions


class HWAccelStrategy(ABC):
    config: EncoderConfig

    async def input_args(self, needs_scale: bool) -> list[str]:
        cmd: list[str] = []
        if self.config.hwaccel:
            cmd.extend(["-hwaccel", self.config.hwaccel])
            if self.config.hwaccel_output_format and not needs_scale:
                cmd.extend(
                    ["-hwaccel_output_format", self.config.hwaccel_output_format]
                )
        return cmd

    def video_filters(self, needs_scale: bool) -> list[str]:
        return []

    @abstractmethod
    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        raise NotImplementedError

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
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
