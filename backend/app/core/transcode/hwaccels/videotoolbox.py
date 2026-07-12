from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    hardware_rotation_direction,
    software_geometry_filters,
    software_tonemap_filters,
)


class VideoToolbox(HWAccelStrategy):
    """Apple VideoToolbox H.264 encoding strategy."""

    def keep_hardware_frames(self, context: TranscodeContext) -> bool:
        """Keep HDR frames on VideoToolbox even when scaling is required."""
        if context.is_interlaced or context.needs_sar_normalization:
            return False
        if context.needs_rotation and not context.uses_hardware_filters:
            return False
        return context.needs_tonemap or super().keep_hardware_frames(context)

    def hardware_transform_filters(self, context: TranscodeContext) -> list[str]:
        """Rotate progressive square-pixel VideoToolbox frames on-device."""
        if (
            context.is_interlaced
            or context.needs_sar_normalization
            or (context.needs_resolution_scale and not context.needs_tonemap)
        ):
            return []
        direction = hardware_rotation_direction(context.rotation)
        if direction is None:
            return []
        return [f"transpose_vt=dir={direction}"]

    def hardware_transform_filter_names(self, context: TranscodeContext) -> set[str]:
        return {"transpose_vt"} if self.hardware_transform_filters(context) else set()

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Normalize non-YUV420P VideoToolbox frames to NV12."""
        hardware_filters = (
            self.hardware_transform_filters(context)
            if context.uses_hardware_filters
            else []
        )
        cpu_geometry = (
            context.is_interlaced
            or context.needs_sar_normalization
            or (context.needs_rotation and not context.uses_hardware_filters)
            or (context.needs_resolution_scale and not context.needs_tonemap)
        )
        if cpu_geometry:
            if context.needs_tonemap:
                filters = software_geometry_filters(context, include_scale=False)
                filters.extend(software_tonemap_filters(context, "nv12"))
                if context.needs_scale:
                    filters.append("setsar=1")
                return filters
            return [*software_geometry_filters(context), "format=nv12"]
        if not context.uses_hardware_decode:
            if context.needs_tonemap:
                return software_tonemap_filters(context, "nv12")
            return ["format=nv12"]
        if context.needs_tonemap:
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
