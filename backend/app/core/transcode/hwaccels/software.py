from app.core.transcode.hwaccels.base import HWAccelStrategy, TranscodeContext
from app.core.transcode.options import HW_BITRATE


class Software(HWAccelStrategy):
    """Software H.264 strategy based on libx264."""

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build libx264 CRF options with a VBV bitrate cap.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for predictable-bandwidth software encoding.
        """
        options = context.options
        bitrate = HW_BITRATE.get(options.quality, "3000k")
        bitrate_num = int(bitrate[:-1])
        bufsize = str(bitrate_num * 2) + "k"
        return [
            "-preset",
            "veryfast",
            "-crf",
            str(options.crf),
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            # cap CRF bitrate spikes to keep streaming bandwidth predictable
            "-maxrate",
            bitrate,
            "-bufsize",
            bufsize,
        ]

    def keyframe_args(self, context: TranscodeContext) -> list[str]:
        """Build segment-timed keyframes without scene-change insertion.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for deterministic segment-aligned keyframes.
        """
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{context.segment_length})",
            # disable scene-change keyframes for deterministic GOPs
            "-sc_threshold:v:0",
            "0",
        ]
