from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    cpu_geometry_filters,
    cpu_tonemap_filters,
    resolve_vaapi_device,
    rotation_direction,
    segment_keyframe_args,
)


class VAAPI(HWAccelStrategy):
    """Linux VAAPI H.264 encoding strategy."""

    async def resolve_device(self, context: TranscodeContext) -> str | None:
        """Resolve the required VAAPI DRM render node."""
        device = await resolve_vaapi_device()
        if not device:
            raise RuntimeError(
                "VAAPI requires a DRM render device, e.g. /dev/dri/renderD128"
            )
        return device

    def encoder_probe_args(self, context: TranscodeContext) -> list[str]:
        """Build a synthetic VAAPI upload and encode probe."""
        device = context.device
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
            return not (
                (context.needs_scale or context.needs_rotation or context.is_interlaced)
                and not context.uses_hw_filters
            )
        if context.is_hlg:
            return False
        return super().keep_hardware_frames(context)

    def transform_filters(self, context: TranscodeContext) -> list[str]:
        """Build eligible VAAPI deinterlace, rotation, and scale filters."""
        if context.is_hlg:
            return []
        filters: list[str] = []
        if context.is_interlaced:
            filters.extend(["deinterlace_vaapi=rate=frame:auto=0", "setfield=prog"])
        direction = rotation_direction(context.rotation)
        if direction is not None:
            filters.append(f"transpose_vaapi=dir={direction}")
        if context.needs_scale:
            width = context.scale_width
            height = context.scale_height
            assert width is not None and height is not None
            scale = f"scale_vaapi=w={width}:h={height}"
            if not context.needs_tonemap:
                scale += ":format=nv12"
            filters.extend([scale, "setsar=1"])
        return filters

    def transform_filter_names(self, context: TranscodeContext) -> set[str]:
        """Return filter names required by the selected VAAPI transforms."""
        filters = self.transform_filters(context)
        names: set[str] = set()
        if any(value.startswith("deinterlace_vaapi") for value in filters):
            names.update({"deinterlace_vaapi", "setfield"})
        if any(value.startswith("transpose_vaapi") for value in filters):
            names.add("transpose_vaapi")
        if any(value.startswith("scale_vaapi") for value in filters):
            names.update({"scale_vaapi", "setsar"})
        return names

    def transform_download_format(self, context: TranscodeContext) -> str:
        """Return the CPU format produced after downloading VAAPI transforms."""
        if context.needs_scale and not context.needs_tonemap:
            return "nv12"
        return super().transform_download_format(context)

    async def input_args(self, context: TranscodeContext) -> list[str]:
        """Configure VAAPI decoding and select its render device.

        Hardware frames stay on the VAAPI device unless required transforms
        must use software filters; otherwise FFmpeg exposes decoded frames to the
        shared software path.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg hardware-decoding and device-selection options.

        Raises:
            RuntimeError: If no usable DRM render device is available.
        """
        vaapi_dev = context.device or await self.resolve_device(context)
        assert vaapi_dev is not None
        cmd = await super().input_args(context)
        cmd.extend(["-vaapi_device", vaapi_dev])
        return cmd

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Normalize frames to NV12 on the active memory path.

        Frames retained on VAAPI use hardware transforms directly. Frames
        exposed to system memory use shared software filters and are uploaded
        before encoding.

        Args:
            context: The runtime transcode context.

        Returns:
            Hardware or software NV12 conversion filters for VAAPI encoding.
        """
        hardware_filters = (
            self.transform_filters(context) if context.uses_hw_filters else []
        )
        cpu_geometry = (
            context.needs_scale or context.needs_rotation or context.is_interlaced
        ) and not context.uses_hw_filters
        if context.is_hdr10:
            # vaapi tone mapping follows either hardware or CPU geometry
            if cpu_geometry:
                return [
                    *cpu_geometry_filters(context),
                    "format=p010",
                    "hwupload",
                    "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709",
                ]
            filters = list(hardware_filters)
            if not context.uses_hw_decode:
                filters.extend(["format=p010", "hwupload"])
            filters.append("tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709")
            return filters
        if context.is_hlg:
            # standard FFmpeg handles HLG tone mapping in system memory
            filters = cpu_geometry_filters(context, include_scale=False)
            filters.extend(cpu_tonemap_filters(context, "nv12"))
            if context.needs_scale:
                filters.append("setsar=1")
            filters.append("hwupload")
            return filters
        if cpu_geometry:
            # software geometry must upload NV12 frames before VAAPI encoding
            return [
                *cpu_geometry_filters(context),
                "format=nv12",
                "hwupload",
            ]
        if context.needs_scale:
            return hardware_filters
        if not context.uses_hw_decode:
            return ["format=nv12", "hwupload"]
        return [*hardware_filters, "scale_vaapi=format=nv12"]

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
        return segment_keyframe_args(context)
