from dataclasses import dataclass
from typing import ClassVar, Literal


@dataclass
class EncoderConfig:
    """FFmpeg codec and decoding options for an acceleration strategy.

    Attributes:
        encoder: The FFmpeg video encoder name.
        hwaccel: The FFmpeg hardware decoder name.
        hwaccel_output_format: The hardware frame format requested from the decoder.
    """

    encoder: str = "libx264"
    hwaccel: str | None = None
    hwaccel_output_format: str | None = None


HWAccelType = Literal["qsv", "vaapi", "nvenc", "videotoolbox"]
ENCODER_CONFIG: dict[str | None, EncoderConfig] = {
    None: EncoderConfig(),
    "qsv": EncoderConfig(
        encoder="h264_qsv",
        hwaccel="qsv",
        hwaccel_output_format="qsv",
    ),
    "vaapi": EncoderConfig(
        encoder="h264_vaapi",
        hwaccel="vaapi",
        hwaccel_output_format="vaapi",
    ),
    "nvenc": EncoderConfig(
        encoder="h264_nvenc",
        hwaccel="cuda",
        hwaccel_output_format="cuda",
    ),
    "videotoolbox": EncoderConfig(
        encoder="h264_videotoolbox",
        hwaccel="videotoolbox",
        hwaccel_output_format="videotoolbox_vld",
    ),
}

QualityLevel = Literal["low", "medium", "high"]
QUALITY_CRF: dict[QualityLevel, int] = {
    "low": 28,
    "medium": 23,
    "high": 18,
}
HW_BITRATE: dict[QualityLevel, str] = {
    "low": "1500k",
    "medium": "3000k",
    "high": "6000k",
}

ResolutionLimit = Literal["original", "1080p", "720p", "480p"]
RESOLUTION_MAX_HEIGHT: dict[ResolutionLimit, int | None] = {
    "original": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}


@dataclass
class TranscodeOptions:
    """Validated transcoding parameters for web playback.

    Attributes:
        segment_length: The fixed HLS segment duration in seconds.
        hwaccel: The selected hardware acceleration strategy.
        quality: The requested quality preset.
        resolution: The maximum output resolution.
    """

    segment_length: ClassVar[int] = 6

    hwaccel: HWAccelType | None = None
    quality: QualityLevel = "medium"
    resolution: ResolutionLimit = "original"

    def __post_init__(self):
        """Validate that every selected option has a known configuration.

        Raises:
            ValueError: If quality, resolution, or hardware acceleration is unsupported.
        """
        if self.quality not in QUALITY_CRF:
            raise ValueError(f"Invalid transcode quality: {self.quality!r}")
        if self.resolution not in RESOLUTION_MAX_HEIGHT:
            raise ValueError(f"Invalid transcode resolution: {self.resolution!r}")
        if self.hwaccel not in ENCODER_CONFIG:
            raise ValueError(f"Invalid transcode hwaccel: {self.hwaccel!r}")

    @property
    def encoder_config(self) -> EncoderConfig:
        return ENCODER_CONFIG[self.hwaccel]

    @property
    def encoder(self) -> str:
        return self.encoder_config.encoder

    @property
    def crf(self) -> int:
        return QUALITY_CRF[self.quality]

    @property
    def bitrate(self) -> str:
        return HW_BITRATE[self.quality]

    @property
    def max_height(self) -> int | None:
        return RESOLUTION_MAX_HEIGHT[self.resolution]

    @property
    def profile(self) -> str:
        """Transcode profile identifier derived from the selected settings."""
        return f"{self.quality}_{self.resolution}_{self.hwaccel or 'none'}"
