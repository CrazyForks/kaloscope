from dataclasses import dataclass
from typing import Literal


@dataclass
class EncoderConfig:
    """Configuration for a video encoder and its acceleration options."""

    encoder: str = "libx264"
    hwaccel: str | None = None
    hwaccel_output_format: str | None = None


# hardware acceleration types (mapped to encoder name and ffmpeg flags)
HWAccelType = Literal["qsv", "vaapi", "nvenc", "videotoolbox"]
ENCODER_CONFIG: dict[str | None, EncoderConfig] = {
    None: EncoderConfig(
        encoder="libx264",
        hwaccel=None,
        hwaccel_output_format=None,
    ),
    "qsv": EncoderConfig(
        encoder="h264_qsv",
        hwaccel="qsv",
        hwaccel_output_format="qsv",
    ),
    "vaapi": EncoderConfig(
        encoder="h264_vaapi",
        hwaccel="vaapi",
        hwaccel_output_format=None,
    ),
    "nvenc": EncoderConfig(
        encoder="h264_nvenc",
        hwaccel="cuda",
        hwaccel_output_format="cuda",
    ),
    "videotoolbox": EncoderConfig(
        encoder="h264_videotoolbox",
        hwaccel="videotoolbox",
        hwaccel_output_format="videotoolbox",
    ),
}

# transcode quality levels (mapped to CRF values and bitrate targets)
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

# output resolution limits (mapped to max height in pixels)
ResolutionLimit = Literal["original", "1080p", "720p", "480p"]
RESOLUTION_MAX_HEIGHT: dict[ResolutionLimit, int | None] = {
    "original": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}


@dataclass
class TranscodeOptions:
    """Transcoding parameters for web playback."""

    hwaccel: HWAccelType | None = None
    quality: QualityLevel = "medium"
    resolution: ResolutionLimit = "original"
    framerate: float = 30.0

    def __post_init__(self):
        if self.hwaccel not in ENCODER_CONFIG:
            self.hwaccel = None

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
    def max_height(self) -> int | None:
        return RESOLUTION_MAX_HEIGHT[self.resolution]

    @property
    def profile(self) -> str:
        """Transcode profile identifier derived from the selected settings."""
        return f"{self.quality}_{self.resolution}_{str(self.hwaccel).lower()}"
