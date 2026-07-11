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
        if context.supports_hwaccel("qsv") and (
            context.needs_tonemap or not context.needs_scale
        ):
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
        if context.needs_tonemap:
            filters: list[str] = []
            if not context.uses_hardware_decode:
                filters.extend(["format=p010le", "hwupload"])
            value = (
                "vpp_qsv=tonemap=1:format=nv12:out_color_matrix=bt709:"
                "out_color_primaries=bt709:out_color_transfer=bt709"
            )
            if context.needs_scale:
                value += f":w='{context.scale_width}':h='{context.scale_height}'"
            filters.append(value)
            return filters
        if context.needs_scale or not context.uses_hardware_decode:
            return ["format=nv12"]
        return ["vpp_qsv=format=nv12"]

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
        args: list[str] = []
        if context.supports_encoder_option("preset"):
            args.extend(["-preset", "veryfast"])
        args.extend(
            [
                "-b:v",
                bitrate,
                "-maxrate",
                str(bitrate_num + 1) + "k",
                "-bufsize",
                str(bitrate_num * 2 * 2) + "k",
            ]
        )
        if context.supports_encoder_option("mbbrc"):
            args.extend(["-mbbrc", "1"])
        if context.supports_encoder_option("rc_init_occupancy"):
            args.extend(["-rc_init_occupancy", str(bitrate_num * 2 * 1000)])
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
