from app.core.transcode.hwaccels.base import HWAccelStrategy, resolve_vaapi_device
from app.core.transcode.options import ENCODER_CONFIG, TranscodeOptions


class VAAPI(HWAccelStrategy):
    """Linux VAAPI H.264 encoding strategy."""

    config = ENCODER_CONFIG["vaapi"]

    async def input_args(self, needs_scale: bool) -> list[str]:
        """Select the VAAPI device used to upload frames for encoding.

        Args:
            needs_scale: Whether the transcode uses a software scaling filter.

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

    def video_filters(self, needs_scale: bool) -> list[str]:
        """Convert system-memory frames to NV12 and upload them to VAAPI.

        Args:
            needs_scale: Whether the transcode uses a software scaling filter.

        Returns:
            The NV12 conversion and hardware upload filters.
        """
        return ["format=nv12", "hwupload"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        """Build broadly compatible VAAPI constant-QP options.

        Args:
            options: The requested transcode settings.

        Returns:
            FFmpeg CQP options using the quality CRF as the QP target.
        """
        # prefer CQP because bitrate modes vary across VAAPI drivers
        return [
            "-rc_mode",
            "CQP",
            "-qp",
            str(options.crf),
        ]

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        """Build timestamp-based keyframe placement for VAAPI.

        Args:
            options: The requested transcode settings.
            seg_len: The target HLS segment duration in seconds.

        Returns:
            FFmpeg options that force keyframes at segment boundaries.
        """
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{seg_len})",
        ]
