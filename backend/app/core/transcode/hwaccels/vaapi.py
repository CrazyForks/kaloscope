from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    resolve_vaapi_device,
)


class VAAPI(HWAccelStrategy):
    """Linux VAAPI H.264 encoding strategy."""

    async def input_args(self, context: TranscodeContext) -> list[str]:
        """Select the VAAPI device used to upload frames for encoding.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options selecting the VAAPI device.

        Raises:
            RuntimeError: If no usable DRM render device is available.
        """
        vaapi_dev = await resolve_vaapi_device()
        if not vaapi_dev:
            raise RuntimeError(
                "VAAPI requires a DRM render device, e.g. /dev/dri/renderD128"
            )
        return ["-vaapi_device", vaapi_dev]

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Convert system-memory frames to NV12 and upload them to VAAPI.

        Args:
            context: The runtime transcode context.

        Returns:
            The NV12 conversion and hardware upload filters.
        """
        return ["format=nv12", "hwupload"]

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build broadly compatible VAAPI constant-QP options.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg CQP options using the quality CRF as the QP target.
        """
        # prefer CQP because bitrate modes vary across VAAPI drivers
        return [
            "-rc_mode",
            "CQP",
            "-qp",
            str(context.options.crf),
        ]

    def keyframe_args(self, context: TranscodeContext) -> list[str]:
        """Build timestamp-based keyframe placement for VAAPI.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options that force keyframes at segment boundaries.
        """
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{context.segment_length})",
        ]
