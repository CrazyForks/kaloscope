import math

from app.core.transcode.hwaccels.base import HWAccelStrategy, resolve_vaapi_device
from app.core.transcode.options import (
    ENCODER_CONFIG,
    HW_BITRATE,
    TranscodeOptions,
)


class QSV(HWAccelStrategy):
    """Intel Quick Sync Video H.264 encoding strategy."""

    config = ENCODER_CONFIG["qsv"]

    async def input_args(self, needs_scale: bool) -> list[str]:
        """Initialize QSV through a VAAPI-backed DRM render device.

        Args:
            needs_scale: Whether the transcode uses a software scaling filter.

        Returns:
            FFmpeg device initialization and hardware decoding options.

        Raises:
            RuntimeError: If no usable DRM render device is available.
        """
        qsv_dev = await resolve_vaapi_device()
        if not qsv_dev:
            raise RuntimeError(
                "QSV requires a DRM render device, e.g. /dev/dri/renderD128"
            )
        cmd = [
            "-init_hw_device",
            f"qsv=qs:hw,child_device={qsv_dev},child_device_type=vaapi",
            "-filter_hw_device",
            "qs",
        ]
        if not needs_scale:
            cmd.extend(
                [
                    "-hwaccel",
                    "qsv",
                    "-hwaccel_device",
                    "qs",
                    "-hwaccel_output_format",
                    "qsv",
                ]
            )
        return cmd

    def video_filters(self, needs_scale: bool) -> list[str]:
        """Choose NV12 conversion for the current frame-memory location.

        Args:
            needs_scale: Whether software scaling leaves frames in system memory.

        Returns:
            A software format filter or QSV VPP filter.
        """
        return ["format=nv12" if needs_scale else "vpp_qsv=format=nv12"]

    def encoder_args(self, options: TranscodeOptions) -> list[str]:
        """Build QSV VBR options with conservative buffer sizing.

        Uses the level 5.1-or-newer buffer factor because codec-level detection
        is not available here.

        Args:
            options: The requested transcode settings.

        Returns:
            FFmpeg QSV rate-control and buffer options.
        """
        bitrate = HW_BITRATE.get(options.quality, "3000k")
        bitrate_num = int(bitrate[:-1])
        return [
            "-preset",
            "veryfast",
            "-b:v",
            bitrate,
            "-maxrate",
            str(bitrate_num + 1) + "k",
            "-bufsize",
            str(bitrate_num * 2 * 2) + "k",
            "-mbbrc",
            "1",
            "-rc_init_occupancy",
            str(bitrate_num * 2 * 1000),
        ]

    def keyframe_args(self, options: TranscodeOptions, seg_len: int) -> list[str]:
        """Build a fixed GOP approximating one HLS segment.

        Args:
            options: Transcode settings containing the source frame rate.
            seg_len: The target HLS segment duration in seconds.

        Returns:
            FFmpeg options for fixed GOP and minimum keyframe intervals.
        """
        gop = math.ceil(options.framerate * seg_len)
        return [
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
