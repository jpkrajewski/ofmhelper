# Copyright (C) 2026 jpkrajewski

HDR_TRANSFERS = frozenset({"arib-std-b67", "smpte2084"})
SDR_WHITE_NITS = 203


def sdr_filters(transfer: str | None, primaries: str | None, space: str | None) -> str:
    if transfer not in HDR_TRANSFERS:
        return ""
    return (
        f"zscale=tin={transfer}"
        f":pin={primaries or 'bt2020'}"
        f":min={space or 'bt2020nc'}"
        f":t=bt709:p=bt709:m=bt709:npl={SDR_WHITE_NITS},format=yuv420p,"
    )
