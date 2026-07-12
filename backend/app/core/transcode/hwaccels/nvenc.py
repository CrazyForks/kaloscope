import math

from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
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

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Normalize original-resolution CUDA frames to 8-bit YUV."""
        if context.needs_tonemap:
            filters = software_tonemap_filters(context, "yuv420p")
            if context.supports_filter("hwupload_cuda"):
                filters.append("hwupload_cuda")
            return filters
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
            ]
        )
        return args

    def keyframe_args(self, context: TranscodeContext) -> list[str]:
        """Build a fixed GOP approximating one HLS segment.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for fixed GOP and minimum keyframe intervals.
        """
        gop = math.ceil(context.source_framerate * context.options.segment_length)
        return [
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
