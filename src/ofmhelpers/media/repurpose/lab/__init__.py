"""The knob lab: which knobs (and mixes) hide a copy from the checker while
the copy still looks and sounds like its source. Run on a big CPU box
(a RunPod CPU pod), not in the app:

- `space.py` -- the knobs, their caps ("nearly identical"), strength -> preset;
- `measure.py` -- one copy: detection per method, VMAF, sound change;
- `search.py` -- the CLI: ablation, random throws, Optuna NSGA-II;
- `report.py` -- the CLI after it: CSVs, picked presets, validation,
  side-by-side examples, index.html.

Needs `uv sync --group lab` (optuna) and, for VMAF, an ffmpeg with libvmaf
in `OFM_LAB_VMAF_FFMPEG`.
"""
