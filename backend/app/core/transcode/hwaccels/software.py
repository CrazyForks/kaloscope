from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class Software(HWAccelStrategy):
    """Software H.264 strategy based on libx264."""

    config = ENCODER_CONFIG[None]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        """Build libx264 CRF options with a VBV bitrate cap.

        Args:
            options: The requested transcode settings.

        Returns:
            FFmpeg options for predictable-bandwidth software encoding.
        """
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

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        """Build segment-timed keyframes without scene-change insertion.

        Args:
            options: The requested transcode settings.
            seg_len: The target HLS segment duration in seconds.

        Returns:
            FFmpeg options for deterministic segment-aligned keyframes.
        """
        return [
            "-force_key_frames:0",
            f"expr:gte(t,n_forced*{seg_len})",
            # disable scene-change keyframes for deterministic GOPs
            "-sc_threshold:v:0",
            "0",
        ]
