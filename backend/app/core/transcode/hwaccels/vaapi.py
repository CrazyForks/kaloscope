from app.core.transcode import transcoder
from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.options import ENCODER_CONFIG, TranscodeOptions


class VAAPI(HWAccelStrategy):
    config = ENCODER_CONFIG["vaapi"]

    async def input_args(self, needs_scale: bool) -> list[str]:
        vaapi_dev = await transcoder._vaapi_device()
        if vaapi_dev:
            return ["-vaapi_device", vaapi_dev]
        return await super().input_args(needs_scale)

    def video_filters(self, needs_scale: bool) -> list[str]:
        # VAAPI: ensure NV12 8-bit format and re-upload to GPU for the encoder.
        # HEVC 10-bit decode produces P010 surfaces — format=nv12 converts in
        # software, then hwupload uploads to the device created by -vaapi_device
        # (or the implicit device from -hwaccel vaapi as fallback).
        return ["format=nv12", "hwupload"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        # CQP is the safest RC mode — universally supported across Intel iHD
        # and i965 drivers. VBR / CBR may be unavailable on some GPUs.
        # Reuse CRF values as QP targets (lower = higher quality, ~0–51).
        return [
            "-rc_mode",
            "CQP",
            "-qp",
            str(options.crf),
        ]

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{seg_len})",
        ]
