from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    cpu_geometry_filters,
    cpu_tonemap_filters,
    segment_keyframe_args,
)


class Software(HWAccelStrategy):
    """Software H.264 strategy based on libx264."""

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Tone map HDR input to 8-bit BT.709 in system memory."""
        if context.needs_tonemap:
            filters = cpu_geometry_filters(context, include_scale=False)
            filters.extend(cpu_tonemap_filters(context, "yuv420p"))
            if context.needs_scale:
                filters.append("setsar=1")
            return filters
        return cpu_geometry_filters(context)

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build libx264 CRF options with a VBV bitrate cap.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for predictable-bandwidth software encoding.
        """
        options = context.options
        bitrate = options.bitrate
        bitrate_num = int(bitrate[:-1])
        bufsize = f"{bitrate_num * 2}k"
        args: list[str] = []
        if context.supports_encoder_option("preset"):
            args.extend(["-preset", "veryfast"])
        if context.supports_encoder_option("crf"):
            args.extend(["-crf", str(options.crf)])
        if context.supports_encoder_option("profile"):
            args.extend(["-profile:v", "main"])
        args.extend(
            [
                "-pix_fmt",
                "yuv420p",
                # cap CRF bitrate spikes to keep streaming bandwidth predictable
                "-maxrate",
                bitrate,
                "-bufsize",
                bufsize,
            ]
        )
        return args

    def keyframe_args(self, context: TranscodeContext) -> list[str]:
        """Build segment-timed keyframes without scene-change insertion.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for deterministic segment-aligned keyframes.
        """
        return [
            *segment_keyframe_args(context),
            # disable scene-change keyframes for deterministic GOPs
            "-sc_threshold:v:0",
            "0",
        ]
