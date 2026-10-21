"""SDR sources keep their brightness in the HLG output: SDR white lands on
BT.2408's HDR reference white, 75% HLG signal (10-bit limited code 721)."""

import random
import re
import subprocess

from ofmhelpers.iphones import load_profile
from ofmhelpers.iphones.recorder import _video_filter
from ofmhelpers.utils.capcut import SourceInfo

BT2408_WHITE_CODE = 721  # 64 + 0.75 * (940 - 64)


def test_sdr_white_lands_on_hdr_reference_white():
    profile = load_profile("iphone17")
    src = SourceInfo(duration=1.0, width=1080, height=1920, has_audio=False)
    plan = profile.video.timing.plan(random.Random(1), 30)  # noqa: S311 - not cryptography
    chain = _video_filter(profile, src, plan, mirror=False)

    out = subprocess.run(
        [
            "ffmpeg", "-hide_banner",
            "-f", "lavfi", "-i", "color=c=white:s=1080x1920:d=1:r=30,format=yuv420p",
            "-vf", f"{chain},signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr  # fmt: skip

    luma = [float(v) for v in re.findall(r"YAVG=([0-9.]+)", out)]
    assert luma
    assert abs(luma[0] - BT2408_WHITE_CODE) <= 3
