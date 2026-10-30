"""Checking a copy against its source: `check(source, copy) -> CheckReport`.

Tier 1 (hashes, built 2026-10-26, the notes below applied): the copy
sampled 1 frame/s, the source 10/s, both on timestamps (`frames.py`), flat
frames left out and the rest normalized (borders cropped, grey + equalized,
blurred, 256x256: `normalize.py`); each copy frame matched to its closest
source frame by PDQ / pHash / dHash / wHash, full frame and centre crop on
both sides, all 8 rotations / flips on the query side (`visual.py`); the
matches fit to one time line, speed 0.8-1.25 (`sequence.py`): the copy looks
like the source when half its frames sit on it. Chromaprint audio
fingerprints in ~2 s windows (`audio.py`). Optional, where installed (the
lab pod): SSCD neural descriptors (`sscd.py`, same matching) and Meta's
TMK+PDQF whole-video score (`tmk.py`). A measurement only: nothing is redone.

Not yet: per-scene sampling (PySceneDetect), CLIP + faiss, an AugLy
false-positive set.
"""

from ofmhelpers.repurpose.checker.check import CheckReport, check

__all__ = ["CheckReport", "check"]

# pip install opencv-python imagehash pdqhash pyacoustid faiss-cpu numpy augly
# Use the tiered approach: hashes first, embeddings only for possible matches.
# Sample 1 frame per second or 1 per scene, not every frame.
# Resize frames before embedding (SSCD and CLIP downsize them anyway).
# On a Mac with Apple Silicon, PyTorch's mps backend speeds up embeddings a lot.
# Here's a stack, one layer per stage of the pipeline we discussed:

# 1. Video decoding and sampling

# PyAV or decord: fast frame extraction (decord is quicker for random access to frames)
# scenedetect (PySceneDetect): split into shots and take keyframes per scene
# ffmpeg-python: pull out the audio track and normalize formats

# 2. Cheap hashing (first filter)

# pdqhash: Python bindings for Meta's PDQ, the production-grade choice
# imagehash: pHash and dHash, simple and good for prototyping
# videohash: whole-video hash, a quick baseline

# CapCut Adjust panel -> now video knobs: transformations/adjust.py
# Temp
# Tint
# Saturation
# Lightness
# Exposure
# Contrast
# Highlight
# Shadow
# Whites
# Blacks
# Brilliance
# Effects
# Sharpen
# Clarity
# Particles
# Fade
# Vignette

# Normalize frames before hashing, so edits get undone before comparison:

# Detect and crop borders and padding (look for rows and columns that are almost a single color)
# Convert to grayscale and equalize the histogram. That cancels most color, brightness, contrast, gamma and tint changes.
# Apply a light blur (Gaussian, 3-5 px) to wash out noise, grain and sharpening
# Resize everything to the same small size, like 256x256

# Make hashing robust to framing:

# Mirroring: pdqhash.compute_dihedral gives 8 hashes per frame (rotations and flips). Store one, query with all 8.
# Zoom and shift: also hash a center crop (around 70-80%) of every frame, on both the stored and the query side.
# Raise the Hamming threshold a bit. For PDQ, around 31 is Meta's usual starting point. Check false positives with AugLy test sets.

# Match on sequences, not single frames:

# Speed changes and trimming break frame-by-frame alignment. Sample by scene changes instead of fixed time, or allow a time-scaling factor (say 0.8-1.25) when checking whether matches line up.
# Call it a match when enough frames match in order, not all of them.
