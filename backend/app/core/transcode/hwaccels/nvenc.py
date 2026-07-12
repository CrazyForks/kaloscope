from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    cpu_geometry_filters,
    cpu_tonemap_filters,
)


class NVENC(HWAccelStrategy):
    """NVIDIA NVENC H.264 encoding strategy."""

    async def resolve_device(self, context: TranscodeContext) -> str | None:
        """Use the default CUDA device selected by the current command path."""
        return "0"

    def keep_hardware_frames(self, context: TranscodeContext) -> bool:
        """Return system frames when HDR requires the standard CPU filter."""
        return not context.needs_tonemap and super().keep_hardware_frames(context)

    def transform_filters(self, context: TranscodeContext) -> list[str]:
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

    def transform_filter_names(self, context: TranscodeContext) -> set[str]:
        """Return filter names required by the selected CUDA transforms."""
        filters = self.transform_filters(context)
        names: set[str] = set()
        if any(value.startswith("yadif_cuda") for value in filters):
            names.update({"setfield", "yadif_cuda"})
        if any(value.startswith("scale_cuda") for value in filters):
            names.update({"scale_cuda", "setsar"})
        return names

    def transform_download_format(self, context: TranscodeContext) -> str:
        """Return the CPU format produced after downloading CUDA transforms."""
        if context.needs_scale and not context.needs_tonemap:
            return "yuv420p"
        return super().transform_download_format(context)

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Build CUDA or CPU filters and normalize output to 8-bit YUV.

        Args:
            context: The runtime transcode context.

        Returns:
            Filters for the prepared frame-memory and geometry path.
        """
        if context.uses_hw_filters:
            # successful source probing allows transforms to stay on CUDA
            filters = self.transform_filters(context)
            if not context.needs_scale:
                filters.append("scale_cuda=format=yuv420p")
            return filters
        if context.needs_tonemap:
            # standard FFmpeg tone mapping runs in system memory before optional upload
            filters = cpu_geometry_filters(context, include_scale=False)
            filters.extend(cpu_tonemap_filters(context, "yuv420p"))
            if context.needs_scale:
                filters.append("setsar=1")
            if context.supports_filter("hwupload_cuda"):
                filters.append("hwupload_cuda")
            return filters
        if context.needs_cpu_geometry:
            # unsupported CUDA geometry falls back to the shared CPU chain
            return [*cpu_geometry_filters(context), "format=yuv420p"]
        if not context.needs_scale:
            if context.uses_hw_decode:
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
        nvenc_preset = {"medium": "p4", "high": "p7"}.get(options.quality, "p1")
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
                f"{int(bitrate[:-1]) * 2}k",
                "-forced-idr",
                "1",
            ]
        )
        return args
