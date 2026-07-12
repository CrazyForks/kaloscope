from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    resolve_vaapi_device,
    software_tonemap_filters,
)


class VAAPI(HWAccelStrategy):
    """Linux VAAPI H.264 encoding strategy."""

    async def resolve_hardware_device(self, context: TranscodeContext) -> str | None:
        """Resolve the required VAAPI DRM render node."""
        device = await resolve_vaapi_device()
        if not device:
            raise RuntimeError(
                "VAAPI requires a DRM render device, e.g. /dev/dri/renderD128"
            )
        return device

    def encoder_probe_args(
        self, context: TranscodeContext, device: str | None
    ) -> list[str]:
        """Build a synthetic VAAPI upload and encode probe."""
        assert device is not None
        return [
            "-vaapi_device",
            device,
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=1",
            "-vf",
            "format=nv12,hwupload",
            "-frames:v",
            "1",
            "-an",
            "-c:v",
            context.options.encoder,
            "-f",
            "null",
            "-",
        ]

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
        vaapi_dev = (
            context.hardware.device
            if context.hardware is not None
            else await self.resolve_hardware_device(context)
        )
        assert vaapi_dev is not None
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
            if not context.uses_hardware_decode:
                filters.extend(["format=p010", "hwupload"])
            if context.needs_scale:
                filters.append(
                    f"scale_vaapi=w='{context.scale_width}':h='{context.scale_height}'"
                )
            filters.append("tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709")
            return filters
        if context.is_hlg:
            return [*software_tonemap_filters(context, "nv12"), "hwupload"]
        if context.needs_scale or not context.uses_hardware_decode:
            return ["format=nv12", "hwupload"]
        return ["scale_vaapi=format=nv12"]

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build VAAPI bitrate options with automatic rate-control selection.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg target, maximum, and buffer bitrate options.
        """
        bitrate = context.options.bitrate
        bitrate_num = int(bitrate[:-1])
        return [
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            f"{bitrate_num * 2}k",
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
