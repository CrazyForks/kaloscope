from app.core.transcode.hwaccels.base import HWAccelStrategy, resolve_vaapi_device
from app.core.transcode.options import ENCODER_CONFIG, TranscodeOptions


class VAAPI(HWAccelStrategy):
    config = ENCODER_CONFIG["vaapi"]

    async def input_args(self, needs_scale: bool) -> list[str]:
        vaapi_dev = await resolve_vaapi_device()
        if vaapi_dev:
            return ["-vaapi_device", vaapi_dev]
        return await super().input_args(needs_scale)

    def video_filters(self, needs_scale: bool) -> list[str]:
        # Normalize decoded frames to NV12 in system memory, then upload them to
        # the device created by -vaapi_device (or the implicit VAAPI device).
        return ["format=nv12", "hwupload"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        # Prefer CQP because bitrate-controlled modes such as VBR and CBR may be
        # unavailable with some VAAPI drivers.
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
