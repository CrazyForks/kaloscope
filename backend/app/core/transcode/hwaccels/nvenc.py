from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    software_geometry_filters,
    software_tonemap_filters,
)


class NVENC(HWAccelStrategy):
    """NVIDIA NVENC H.264 encoding strategy."""

    async def resolve_hardware_device(self, context: TranscodeContext) -> str | None:
        """Use the default CUDA device selected by the current command path."""
        return "0"

    def keep_hardware_frames(self, context: TranscodeContext) -> bool:
        """Return system frames when HDR requires the standard CPU filter."""
        return not context.needs_tonemap and super().keep_hardware_frames(context)

    def hardware_transform_filters(self, context: TranscodeContext) -> list[str]:
        """Build eligible CUDA deinterlace and scale filters."""
        parity = context.field_parity
        if context.needs_rotation or context.needs_tonemap:
            return []
        filters: list[str] = []
        if parity is not None:
            filters.extend(
                [
                    f"yadif_cuda=mode=send_frame:parity={parity}:deint=all",
                    "setfield=prog",
                ]
            )
        if context.needs_scale:
            width = context.scale_width
            height = context.scale_height
            assert width is not None and height is not None
            filters.extend(
                [
                    f"scale_cuda=w={width}:h={height}:format=yuv420p",
                    "setsar=1",
                ]
            )
        return filters

    def hardware_transform_filter_names(self, context: TranscodeContext) -> set[str]:
        filters = self.hardware_transform_filters(context)
        names: set[str] = set()
        if any(value.startswith("yadif_cuda") for value in filters):
            names.update({"setfield", "yadif_cuda"})
        if any(value.startswith("scale_cuda") for value in filters):
            names.update({"scale_cuda", "setsar"})
        return names

    def hardware_transform_download_format(self, context: TranscodeContext) -> str:
        if context.needs_scale and not context.needs_tonemap:
            return "yuv420p"
        return super().hardware_transform_download_format(context)

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Normalize original-resolution CUDA frames to 8-bit YUV."""
        if context.uses_hardware_filters:
            filters = self.hardware_transform_filters(context)
            if not context.needs_scale:
                filters.append("scale_cuda=format=yuv420p")
            return filters
        if context.needs_tonemap:
            filters = software_geometry_filters(context, include_scale=False)
            filters.extend(software_tonemap_filters(context, "yuv420p"))
            if context.needs_scale:
                filters.append("setsar=1")
            if context.supports_filter("hwupload_cuda"):
                filters.append("hwupload_cuda")
            return filters
        if context.needs_software_geometry:
            return [*software_geometry_filters(context), "format=yuv420p"]
        if not context.needs_scale:
            if context.uses_hardware_decode:
                return ["scale_cuda=format=yuv420p"]
            return ["format=yuv420p"]
        return []

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build NVENC preset and constrained bitrate options.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for the selected quality preset and bitrate.
        """
        context.require_encoder_option("forced-idr")
        options = context.options
        bitrate = options.bitrate
        nvenc_preset = (
            "p4"
            if options.quality == "medium"
            else ("p7" if options.quality == "high" else "p1")
        )
        args: list[str] = []
        if context.supports_encoder_option("preset"):
            args.extend(["-preset", nvenc_preset])
        args.extend(
            [
                "-b:v",
                bitrate,
                "-maxrate",
                bitrate,
                "-bufsize",
                str(int(bitrate[:-1]) * 2) + "k",
                "-forced-idr",
                "1",
            ]
        )
        return args
