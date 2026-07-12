from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    cpu_geometry_filters,
    cpu_tonemap_filters,
    rotation_direction,
)


class VideoToolbox(HWAccelStrategy):
    """Apple VideoToolbox H.264 encoding strategy."""

    def keep_hardware_frames(self, context: TranscodeContext) -> bool:
        """Keep HDR frames on VideoToolbox even when scaling is required."""
        if context.is_interlaced:
            return False
        if (
            context.needs_square_pixels or context.needs_rotation
        ) and not context.uses_hw_filters:
            return False
        return context.needs_tonemap or super().keep_hardware_frames(context)

    def transform_filters(self, context: TranscodeContext) -> list[str]:
        """Build eligible VideoToolbox rotation and SDR scale filters."""
        if context.is_interlaced or (
            context.needs_tonemap and context.needs_square_pixels
        ):
            return []
        filters: list[str] = []
        direction = rotation_direction(context.rotation)
        if direction is not None:
            filters.append(f"transpose_vt=dir={direction}")
        if context.needs_scale and not context.needs_tonemap:
            width = context.scale_width
            height = context.scale_height
            assert width is not None and height is not None
            filters.extend([f"scale_vt=w={width}:h={height}", "setsar=1"])
        return filters

    def transform_filter_names(self, context: TranscodeContext) -> set[str]:
        """Return filter names required by selected VideoToolbox transforms."""
        filters = self.transform_filters(context)
        names: set[str] = set()
        if any(value.startswith("transpose_vt") for value in filters):
            names.add("transpose_vt")
        if any(value.startswith("scale_vt") for value in filters):
            names.update({"scale_vt", "setsar"})
        return names

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Build VideoToolbox or CPU filters and normalize output to NV12.

        Args:
            context: The runtime transcode context.

        Returns:
            Filters for the prepared frame-memory, HDR, and geometry path.
        """
        hardware_filters = (
            self.transform_filters(context) if context.uses_hw_filters else []
        )
        direct_hdr_scale = (
            context.needs_tonemap
            and context.needs_downscale
            and not context.needs_square_pixels
            and not context.needs_rotation
        )
        # interlace and unsupported geometry force transforms into system memory
        cpu_geometry = context.is_interlaced or (
            (context.needs_scale or context.needs_rotation)
            and not context.uses_hw_filters
            and not direct_hdr_scale
        )
        if cpu_geometry:
            if context.needs_tonemap:
                filters = cpu_geometry_filters(context, include_scale=False)
                filters.extend(cpu_tonemap_filters(context, "nv12"))
                if context.needs_scale:
                    filters.append("setsar=1")
                return filters
            return [*cpu_geometry_filters(context), "format=nv12"]
        if not context.uses_hw_decode:
            if context.needs_tonemap:
                return cpu_tonemap_filters(context, "nv12")
            return ["format=nv12"]
        if context.needs_tonemap:
            # eligible hardware frames use VideoToolbox for direct HDR conversion
            options: list[str] = []
            if context.needs_scale:
                options.extend(
                    [
                        f"w='{context.scale_width}'",
                        f"h='{context.scale_height}'",
                    ]
                )
            options.extend(
                [
                    "color_matrix=bt709",
                    "color_primaries=bt709",
                    "color_transfer=bt709",
                ]
            )
            return [
                *hardware_filters,
                f"scale_vt={':'.join(options)}",
                "setsar=1",
            ]

        pixel_format = context.source_pixel_format
        if not context.needs_scale and (
            pixel_format is None or pixel_format.lower() != "yuv420p"
        ):
            return [*hardware_filters, "scale_vt"]
        return hardware_filters

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build VideoToolbox bitrate and speed-priority options.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for bitrate-controlled VideoToolbox encoding.
        """
        quality = context.options.quality
        bitrate = context.options.bitrate
        vt_prio = "0" if quality in ("high", "medium") else "1"
        args = [
            "-b:v",
            bitrate,
            # disable quantizer bounds so bitrate control acts alone
            "-qmin",
            "-1",
            "-qmax",
            "-1",
        ]
        if context.supports_encoder_option("prio_speed"):
            args.extend(["-prio_speed", vt_prio])
        return args
