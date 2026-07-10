import math

from app.core.transcode.hwaccels.base import HWAccelStrategy, resolve_vaapi_device
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class QSV(HWAccelStrategy):
    config = ENCODER_CONFIG["qsv"]

    async def input_args(self, needs_scale: bool) -> list[str]:
        qsv_dev = await resolve_vaapi_device()
        if not qsv_dev:
            raise RuntimeError(
                "QSV requires a DRM render device, e.g. /dev/dri/renderD128"
            )
        cmd = [
            "-init_hw_device",
            f"qsv=qs:hw,child_device={qsv_dev},child_device_type=vaapi",
            "-filter_hw_device",
            "qs",
        ]
        if not needs_scale:
            cmd.extend(
                [
                    "-hwaccel",
                    "qsv",
                    "-hwaccel_device",
                    "qs",
                    "-hwaccel_output_format",
                    "qsv",
                ]
            )
        return cmd

    def video_filters(self, needs_scale: bool) -> list[str]:
        # QSV: when frames stay on the GPU, use QSV VPP to normalize them to NV12.
        # When CPU scaling is requested, keep frames in system memory and let the
        # QSV encoder upload them after the software scale/format conversion.
        return ["format=nv12" if needs_scale else "vpp_qsv=format=nv12"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        bitrate = HW_BITRATE.get(options.quality, "3000k")
        bitrate_num = int(bitrate[:-1])
        # QSV rate control follows Jellyfin:
        # - maxrate = bitrate + 1 triggers VBR for better bitrate allocation
        # - mbbrc 1 enables MacroBlock-level rate control
        # - bufsize = bitrate * 2 * factor, factor=2 (level ≥ 5.1);
        #   Jellyfin uses factor=1 only for level < 5.1; without codec-level
        #   detection we default to factor=2
        # - rc_init_occupancy = bitrate * 1 * factor (2 s initial buffer fill)
        return [
            "-preset",
            "veryfast",
            "-b:v",
            bitrate,
            "-maxrate",
            str(bitrate_num + 1) + "k",
            "-bufsize",
            str(bitrate_num * 2 * 2) + "k",
            "-mbbrc",
            "1",
            "-rc_init_occupancy",
            str(bitrate_num * 2 * 1000),
        ]

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        # GOP size = segment length × framerate, rounded up to ensure each
        # segment contains at least one keyframe
        gop = math.ceil(options.framerate * seg_len)
        return [
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
