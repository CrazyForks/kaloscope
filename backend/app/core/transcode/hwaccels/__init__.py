from app.core.transcode.hwaccels.base import HWAccelStrategy
from app.core.transcode.hwaccels.nvenc import NVENC
from app.core.transcode.hwaccels.qsv import QSV
from app.core.transcode.hwaccels.software import Software
from app.core.transcode.hwaccels.vaapi import VAAPI
from app.core.transcode.hwaccels.videotoolbox import VideoToolbox
from app.core.transcode.options import HWAccelType

_HWACCELS: dict[HWAccelType | None, HWAccelStrategy] = {
    None: Software(),
    "qsv": QSV(),
    "vaapi": VAAPI(),
    "nvenc": NVENC(),
    "videotoolbox": VideoToolbox(),
}


def get_hwaccel(hwaccel: HWAccelType | None) -> HWAccelStrategy:
    return _HWACCELS[hwaccel]
