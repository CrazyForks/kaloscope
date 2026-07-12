from app.core.transcode.hwaccels.base import (
    HWAccelStrategy,
    TranscodeContext,
    cpu_geometry_filters,
    cpu_tonemap_filters,
    resolve_vaapi_device,
    rotation_direction,
)


class QSV(HWAccelStrategy):
    """Intel Quick Sync Video H.264 encoding strategy."""

    async def resolve_device(self, context: TranscodeContext) -> str | None:
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

    def allows_decode(self, context: TranscodeContext) -> bool:
        """Keep HDR sources on CPU decoding."""
        return not context.needs_tonemap and super().allows_decode(context)

    def transform_filters(self, context: TranscodeContext) -> list[str]:
        """Combine eligible SDR transforms in a single QSV VPP filter."""
        if context.needs_tonemap:
            return []
        options: list[str] = []
        if context.is_interlaced:
            options.extend(["deinterlace=advanced", "rate=frame"])
        direction = rotation_direction(context.rotation)
        if direction is not None:
            options.append(f"transpose={direction}")
        if context.needs_scale:
            width = context.scale_width
            height = context.scale_height
            assert width is not None and height is not None
            options.extend([f"w={width}", f"h={height}"])
        if not options:
            return []
        options.append("format=nv12")
        filters = [f"vpp_qsv={':'.join(options)}"]
        if context.is_interlaced:
            filters.append("setfield=prog")
        if context.needs_scale:
            filters.append("setsar=1")
        return filters

    def transform_filter_names(self, context: TranscodeContext) -> set[str]:
        """Return filter names required by the selected QSV transforms."""
        if not self.transform_filters(context):
            return set()
        names = {"vpp_qsv"}
        if context.is_interlaced:
            names.add("setfield")
        if context.needs_scale:
            names.add("setsar")
        return names

    def transform_download_format(self, context: TranscodeContext) -> str:
        """Return the CPU format produced after downloading QSV transforms."""
        if self.transform_filters(context):
            return "nv12"
        return super().transform_download_format(context)

    def keep_decode_on_fallback(self, context: TranscodeContext) -> bool:
        """QSV CPU transforms use the established software decode path."""
        return False

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
            else await self.resolve_device(context)
        )
        assert qsv_dev is not None
        cmd = [
            "-init_hw_device",
            f"qsv=qs:hw,child_device={qsv_dev},child_device_type=vaapi",
            "-filter_hw_device",
            "qs",
        ]
        if context.uses_hw_decode and not context.needs_tonemap:
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
            # standard FFmpeg tone mapping runs in system memory before upload
            filters = cpu_geometry_filters(context, include_scale=False)
            filters.extend(cpu_tonemap_filters(context, "nv12"))
            if context.needs_scale:
                filters.append("setsar=1")
            filters.append("hwupload")
            return filters
        if context.uses_hw_filters:
            # successful source probing allows transforms to stay in QSV VPP
            return self.transform_filters(context)
        if context.needs_cpu_geometry:
            # hardware transform fallback also switches decoding to system memory
            return [*cpu_geometry_filters(context), "format=nv12"]
        if context.needs_scale or not context.uses_hw_decode:
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
        context.require_encoder_option("forced_idr")
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
                f"{bitrate_num + 1}k",
                "-bufsize",
                f"{bitrate_num * 2 * 2}k",
            ]
        )
        if context.supports_encoder_option("mbbrc"):
            args.extend(["-mbbrc", "1"])
        if context.supports_encoder_option("rc_init_occupancy"):
            args.extend(["-rc_init_occupancy", str(bitrate_num * 2 * 1000)])
        args.extend(["-forced_idr", "1"])
        return args
