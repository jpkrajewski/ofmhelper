"""
radio_comms_fx.py

Applies CoD / CS-style radio comms distortion to voice lines.
Takes clean TTS output -> outputs crunchy, bandpassed, compressed "radio" audio.

Usage:
    python radio_comms_fx.py input.wav output.wav --preset cod_clean
    python radio_comms_fx.py input.wav output.wav --preset cs_crunch
    python radio_comms_fx.py input.wav output.wav --preset dying_static

Batch mode (process a whole folder of barks):
    python radio_comms_fx.py --batch ./tts_output ./radio_output --preset cod_clean
"""

import os
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt, resample_poly

# ---------------------------------------------------------------------------
# Core DSP building blocks
# ---------------------------------------------------------------------------


def bandpass(audio, sr, low_hz, high_hz, order=4):
    """Classic radio speaker bandwidth. Cuts sub-bass and harsh highs."""
    nyq = sr / 2
    low = max(low_hz / nyq, 0.001)
    high = min(high_hz / nyq, 0.999)
    sos = butter(order, [low, high], btype="band", output="sos")
    return sosfilt(sos, audio)


def soft_clip_distortion(audio, drive=3.0):
    """Tanh saturation - warm overdrive, avoids harsh digital clipping."""
    return np.tanh(audio * drive) / np.tanh(drive)


def hard_clip_distortion(audio, threshold=0.5):
    """Harsher, more digital-sounding clip. Good for CS-style crunch."""
    return np.clip(audio, -threshold, threshold) / threshold


def bitcrush(audio, bit_depth=8):
    """Reduces bit depth for a lo-fi/digital radio artifact sound."""
    levels = 2**bit_depth
    return np.round(audio * levels) / levels


def sample_rate_reduce(audio, sr, target_sr):
    """Downsample then upsample back - creates aliasing/lo-fi crunch."""
    down = resample_poly(audio, target_sr, sr)
    up = resample_poly(down, sr, target_sr)
    # pad/trim to match original length
    if len(up) < len(audio):
        up = np.pad(up, (0, len(audio) - len(up)))
    else:
        up = up[: len(audio)]
    return up


def add_static_noise(audio, amount=0.02, seed=None):
    """White noise floor, like radio static/hiss."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1, len(audio))
    return audio + noise * amount


def add_crackle(audio, sr, density=0.001, seed=None):
    """Random impulse crackle/pop artifacts, like a bad connection."""
    rng = np.random.default_rng(seed)
    crackle = np.zeros_like(audio)
    n_pops = int(len(audio) * density)
    positions = rng.integers(0, len(audio), n_pops)
    crackle[positions] = rng.uniform(-0.6, 0.6, n_pops)
    return audio + crackle


def compressor(audio, threshold=0.2, ratio=8.0, makeup_db=6.0):
    """Simple feed-forward compressor for that squashed radio dynamic range."""
    sign = np.sign(audio)
    mag = np.abs(audio)
    over = mag > threshold
    compressed = np.where(
        over,
        threshold + (mag - threshold) / ratio,
        mag,
    )
    out = sign * compressed
    makeup = 10 ** (makeup_db / 20)
    return out * makeup


def tremolo_dropout(audio, sr, rate_hz=8.0, depth=0.3, seed=None):
    """Subtle signal-cutting-out wobble, like weak reception."""
    t = np.arange(len(audio)) / sr
    rng = np.random.default_rng(seed)
    jitter = rng.normal(1.0, 0.05, len(t))
    lfo = 1.0 - depth * (0.5 + 0.5 * np.sin(2 * np.pi * rate_hz * t * jitter))
    return audio * lfo


def normalize(audio, peak=0.9):
    m = np.max(np.abs(audio)) + 1e-9
    return audio / m * peak


# ---------------------------------------------------------------------------
# Presets - tune these to taste
# ---------------------------------------------------------------------------

PRESETS = {
    # Clean-ish military radio - readable, still gritty. Good for squad comms.
    "cod_clean": dict(
        bp_low=400,
        bp_high=3200,
        bp_order=4,
        drive=2.0,
        clip_mode="soft",
        bitdepth=None,
        target_sr=11025,
        noise_amount=0.008,
        crackle_density=0.0002,
        comp_threshold=0.25,
        comp_ratio=6,
        comp_makeup=5,
        tremolo=False,
    ),
    # Crunchier, more aggressive - CS-style comms
    "cs_crunch": dict(
        bp_low=300,
        bp_high=3400,
        bp_order=6,
        drive=4.5,
        clip_mode="hard",
        bitdepth=8,
        target_sr=8000,
        noise_amount=0.015,
        crackle_density=0.0008,
        comp_threshold=0.15,
        comp_ratio=10,
        comp_makeup=7,
        tremolo=False,
    ),
    # Signal cutting out, heavy static - "man down" / low health radio
    "dying_static": dict(
        bp_low=350,
        bp_high=3000,
        bp_order=4,
        drive=3.5,
        clip_mode="hard",
        bitdepth=6,
        target_sr=7000,
        noise_amount=0.05,
        crackle_density=0.003,
        comp_threshold=0.15,
        comp_ratio=12,
        comp_makeup=8,
        tremolo=True,
    ),
    # Long-range/far-off radio, thin and weak
    "long_range": dict(
        bp_low=600,
        bp_high=2600,
        bp_order=6,
        drive=2.5,
        clip_mode="soft",
        bitdepth=8,
        target_sr=8000,
        noise_amount=0.025,
        crackle_density=0.0015,
        comp_threshold=0.2,
        comp_ratio=8,
        comp_makeup=6,
        tremolo=True,
    ),
}


# ---------------------------------------------------------------------------
# Main processing chain
# ---------------------------------------------------------------------------


def _jitter_preset(p, rng, amount=0.18):
    """Randomly nudges every numeric knob by +/- `amount` (18% default) so
    each variation has a genuinely different character, not just different
    noise placement."""

    def j(val):
        return val * (1.0 + rng.uniform(-amount, amount))

    jp = dict(p)  # shallow copy
    jp["bp_low"] = max(50, j(p["bp_low"]))
    jp["bp_high"] = max(jp["bp_low"] + 200, j(p["bp_high"]))
    jp["drive"] = max(0.5, j(p["drive"]))
    if p["bitdepth"]:
        jp["bitdepth"] = int(np.clip(round(j(p["bitdepth"])), 4, 16))
    if p["target_sr"]:
        jp["target_sr"] = int(np.clip(j(p["target_sr"]), 4000, 16000))
    jp["noise_amount"] = max(0.0, j(p["noise_amount"]))
    jp["crackle_density"] = max(0.0, j(p["crackle_density"]))
    jp["comp_threshold"] = float(np.clip(j(p["comp_threshold"]), 0.05, 0.6))
    jp["comp_ratio"] = max(1.5, j(p["comp_ratio"]))
    jp["comp_makeup"] = j(p["comp_makeup"])
    return jp


def process(audio, sr, preset_name, seed=None, jitter=False, jitter_amount=0.18):
    p = PRESETS[preset_name]
    rng_seed = (
        seed if seed is not None else int(np.random.default_rng().integers(0, 2**31))
    )
    rng = np.random.default_rng(rng_seed)

    if jitter:
        p = _jitter_preset(p, rng, amount=jitter_amount)

    out = audio.astype(np.float64)
    out = normalize(out, 0.9)

    out = bandpass(out, sr, p["bp_low"], p["bp_high"], p["bp_order"])

    if p["clip_mode"] == "soft":
        out = soft_clip_distortion(out, drive=p["drive"])
    else:
        out = hard_clip_distortion(out, threshold=1.0 / p["drive"])

    if p["bitdepth"]:
        out = bitcrush(out, p["bitdepth"])

    if p["target_sr"]:
        out = sample_rate_reduce(out, sr, p["target_sr"])

    out = compressor(out, p["comp_threshold"], p["comp_ratio"], p["comp_makeup"])

    if p["tremolo"]:
        out = tremolo_dropout(
            out,
            sr,
            rate_hz=rng.uniform(4, 9),
            depth=rng.uniform(0.2, 0.45),
            seed=rng_seed,
        )

    out = add_static_noise(out, amount=p["noise_amount"], seed=rng_seed)
    out = add_crackle(out, sr, density=p["crackle_density"], seed=rng_seed)

    out = normalize(out, 0.85)
    return out.astype(np.float32)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def process_file(
    in_path, out_path, preset, seed=None, jitter=False, jitter_amount=0.80
):
    audio, sr = sf.read(in_path, always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # mono down, radios are mono
    processed = process(
        audio, sr, preset, seed=seed, jitter=jitter, jitter_amount=jitter_amount
    )
    sf.write(out_path, processed, sr)
    return sr


def generate_variations(
    in_path, out_dir, preset, count=10, jitter_amount=0.18, base_seed=None
):
    """Creates out_dir (if needed) and fills it with `count` randomized
    variations of in_path processed through `preset`. Each variation gets
    its own random seed so noise/crackle/tremolo placement AND the exact
    filter/drive/compression settings differ slightly -> genuinely different
    takes to choose between, not just noise in a different spot."""
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(base_seed)
    stem = os.path.splitext(os.path.basename(in_path))[0]

    results = []
    for i in range(1, count + 1):
        variant_seed = int(rng.integers(0, 2**31))
        out_name = f"{stem}_{preset}_v{i:02d}_seed{variant_seed}.wav"
        out_path = os.path.join(out_dir, out_name)
        process_file(
            in_path,
            out_path,
            preset,
            seed=variant_seed,
            jitter=True,
            jitter_amount=jitter_amount,
        )
        results.append(out_path)
        print(f"[{i}/{count}] {out_name}")

    print(f"\nDone. {count} variations of '{preset}' in: {out_dir}")
    return results
