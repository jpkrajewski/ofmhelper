"""HDR video (HLG as the iPhone records and exports it, or PQ/HDR10) turned
into BT.709 SDR. Treated as SDR without this step, an HDR picture looks dim
and washed out, about 40% less saturated (measured 2026-10-25, llm/CLAUDE.md
Findings). The `colorspace` filter cannot read HLG/PQ transfers, so zscale
is the only route."""

# HDR transfers, as ffprobe's `color_transfer` names them: HLG and PQ (HDR10).
HDR_TRANSFERS = frozenset({"arib-std-b67", "smpte2084"})
# SDR white inside an HDR picture, in nits (ITU-R BT.2408). The iPhone export
# maps SDR white here (iphones recorder `_video_filter`), so converting back
# with it is that export's exact inverse; brighter highlights clip.
SDR_WHITE_NITS = 203


def sdr_filters(transfer: str | None, primaries: str | None, space: str | None) -> str:
    """The ffmpeg filters, ending in ",", that turn a stream with these colour
    tags into BT.709 SDR, or "" when the stream is SDR already. Untagged
    primaries and matrix are taken to be BT.2020."""
    if transfer not in HDR_TRANSFERS:
        return ""
    return (
        f"zscale=tin={transfer}"
        f":pin={primaries or 'bt2020'}"
        f":min={space or 'bt2020nc'}"
        f":t=bt709:p=bt709:m=bt709:npl={SDR_WHITE_NITS},format=yuv420p,"
    )
