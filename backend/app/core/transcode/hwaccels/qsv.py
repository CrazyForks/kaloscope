import math

from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    resolve_vaapi_device,
)


class QSV(HWAccelStrategy):
    """Intel Quick Sync Video H.264 encoding strategy."""

    async def input_args(self, context: TranscodeContext) -> list[str]:
        """Initialize QSV through a VAAPI-backed DRM render device.

        Args:
            context: The runtime transcode context.

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
        if not context.needs_scale:
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

    def video_filters(self, context: TranscodeContext) -> list[str]:
        """Choose NV12 conversion for the current frame-memory location.

        Args:
            context: The runtime transcode context.

        Returns:
            A software format filter or QSV VPP filter.
        """
        return ["format=nv12" if context.needs_scale else "vpp_qsv=format=nv12"]

    def encoder_args(self, context: TranscodeContext) -> list[str]:
        """Build QSV VBR options with conservative buffer sizing.

        Uses the level 5.1-or-newer buffer factor because codec-level detection
        is not available here.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg QSV rate-control and buffer options.
        """
        bitrate = context.options.bitrate
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

    def keyframe_args(self, context: TranscodeContext) -> list[str]:
        """Build a fixed GOP approximating one HLS segment.

        Args:
            context: The runtime transcode context.

        Returns:
            FFmpeg options for fixed GOP and minimum keyframe intervals.
        """
        gop = math.ceil(context.source_framerate * context.segment_length)
        return [
            "-g:v:0",
            str(gop),
            "-keyint_min:v:0",
            str(gop),
        ]
