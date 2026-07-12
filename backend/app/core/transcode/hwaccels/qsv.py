import math

from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    resolve_vaapi_device,
    software_tonemap_filters,
)


class QSV(HWAccelStrategy):
    """Intel Quick Sync Video H.264 encoding strategy."""

    async def resolve_hardware_device(self, context: TranscodeContext) -> str | None:
        """Resolve the required VAAPI-backed QSV render node."""
        device = await resolve_vaapi_device()
        if not device:
            raise RuntimeError(
                "QSV requires a DRM render device, e.g. /dev/dri/renderD128"
            )
        return device

    def encoder_probe_args(
        self, context: TranscodeContext, device: str | None
    ) -> list[str]:
        """Build a synthetic VAAPI-backed QSV upload and encode probe."""
        assert device is not None
        return [
            "-init_hw_device",
            f"qsv=qs:hw,child_device={device},child_device_type=vaapi",
            "-filter_hw_device",
            "qs",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=1",
            "-vf",
            "format=nv12,hwupload",
            "-frames:v",
            "1",
            "-an",
            "-c:v",
            context.options.encoder,
            "-f",
            "null",
            "-",
        ]

    def allows_hardware_decode(self, context: TranscodeContext) -> bool:
        """Keep HDR and software-scaled sources on CPU decoding."""
        return (
            not context.needs_tonemap
            and not context.needs_scale
            and super().allows_hardware_decode(context)
        )

    async def input_args(self, context: TranscodeContext) -> list[str]:
        """Initialize QSV through a VAAPI-backed DRM render device.

        Args:
            context: The runtime transcode context.

        Returns:
            Device initialization plus hardware decoding options for eligible
            SDR input. HDR decoding remains in system memory for CPU tone mapping.

        Raises:
            RuntimeError: If no usable DRM render device is available.
        """
        qsv_dev = (
            context.hardware.device
            if context.hardware is not None
            else await self.resolve_hardware_device(context)
        )
        assert qsv_dev is not None
        cmd = [
            "-init_hw_device",
            f"qsv=qs:hw,child_device={qsv_dev},child_device_type=vaapi",
            "-filter_hw_device",
            "qs",
        ]
        if (
            context.uses_hardware_decode
            and not context.needs_tonemap
            and not context.needs_scale
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
            CPU HDR tone mapping followed by upload, a software format filter,
            or a QSV VPP format filter for hardware-decoded SDR input.
        """
        if context.needs_tonemap:
            return [*software_tonemap_filters(context, "nv12"), "hwupload"]
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
