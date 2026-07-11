from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    resolve_vaapi_device,
    software_tonemap_filters,
)


class VAAPI(HWAccelStrategy):
    """Linux VAAPI H.264 encoding strategy."""

    def keep_hardware_frames(self, context: TranscodeContext) -> bool:
        """Keep HDR10 on VAAPI and expose HLG to the CPU tone mapper."""
        if context.is_hdr10:
            return True
        if context.is_hlg:
            return False
        return super().keep_hardware_frames(context)

    async def input_args(self, context: TranscodeContext) -> list[str]:
        """Configure VAAPI decoding and select its render device.

        Hardware frames stay on the VAAPI device when software scaling is not
        required. Otherwise FFmpeg returns decoded frames to system memory so
        the shared software scale filter can consume them.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg hardware-decoding and device-selection options.

        Raises:
            RuntimeError: If no usable DRM render device is available.
        """
        vaapi_dev = await resolve_vaapi_device()
        if not vaapi_dev:
            raise RuntimeError(
                "VAAPI requires a DRM render device, e.g. /dev/dri/renderD128"
            )
        cmd = await super().input_args(context)
        cmd.extend(["-vaapi_device", vaapi_dev])
        return cmd

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Normalize frames to NV12 on the active memory path.

        VAAPI-decoded frames use the hardware scaler directly. Software-scaled
        frames are converted in system memory and uploaded before encoding.

        Args:
            context: The runtime transcode context.

        Returns:
            Hardware or software NV12 conversion filters for VAAPI encoding.
        """
        if context.is_hdr10:
            filters: list[str] = []
            if context.needs_scale:
                filters.append(
                    f"scale_vaapi=w='{context.scale_width}':h='{context.scale_height}'"
                )
            filters.append("tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709")
            return filters
        if context.is_hlg:
            return [*software_tonemap_filters(context, "nv12"), "hwupload"]
        if context.needs_scale:
            return ["format=nv12", "hwupload"]
        return ["scale_vaapi=format=nv12"]

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
            f"expr:gte(t,n_forced*{context.options.segment_length})",
        ]
