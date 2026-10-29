"""The sound side of a repurposed copy.

`AudioSettings` is the audio part of a preset (every knob optional, missing
= off; values: `repurpose.values`). `AudioTransformer` draws the knobs once
for one copy and builds the -filter:a chain, always in this order:

pitch (asetrate + aresample) -> tempo (the video's speed, pitch undone) ->
volume -> phaser shift -> treble / bass shelves -> echo (reverb or room) ->
mid EQ -> the one obvious effect -> compressor -> de-esser -> make-up gain ->
limiter.

Duplyfi's phaser (in/out gain 0.4 x 0.74), chorus and compressor each cost
5-11 dB (measured 2026-10-26); `match_level` measures the copy against the
source and adds that back before the limiter (`repurpose.Repurpose`).

Knobs come from legacy `utils.uniqify` (volume, pitch) and Duplyfi 1.5.1
(everything else, its default ranges in the presets).
"""

import random

from pydantic import BaseModel

from ofmhelpers.repurpose.values import Value, draw, pick

# A pitch shift under this many semitones stays out of the chain.
_PITCH_MIN = 0.01
_PHASER_DELAY_PER_UNIT = 0.4
_PHASER_DELAY_MS: tuple[float, float] = (0.5, 5.0)
_ROOM_ECHO_SECOND_TAP = 0.7

# Duplyfi's default ranges for the obvious effects (ms or Hz).
_LOUD_RANGE: dict[str, tuple[float, float]] = {
    "chorus": (30, 50),
    "flanger": (2, 10),
    "phaser": (1, 4),
    "tremolo": (2, 8),
    "vibrato": (2, 8),
}


class AudioSettings(BaseModel, frozen=True):
    volume: Value | None = None
    pitch: Value | None = None
    # Duplyfi's "waveform shift" setting; aphaser delay = 0.4 x this (ms).
    phaser_shift: Value | None = None
    treble_db: Value | None = None
    bass_cut_db: Value | None = None
    mid_db: Value | None = None
    # "reverb" / "room", one per copy.
    echo: list[str] | str | None = None
    reverb_ms: Value | None = None
    reverb_decay: Value | None = None
    room_ms: Value | None = None
    # One of chorus / flanger / phaser / tremolo / vibrato / none per copy.
    loud: list[str] | str | None = None
    # Depth of the obvious effect, 1 = Duplyfi's.
    loudness: float = 1.0
    compress_db: Value | None = None
    deess_db: Value | None = None
    limit: Value | None = None
    # A silent source gets faint pink-noise room tone.
    room_tone: bool = False
    # Measure the copy against the source and add the lost level back.
    match_level: bool = False

    def merged(self, extra: "AudioSettings | None") -> "AudioSettings":
        """These settings with `extra`'s set knobs on top."""
        if extra is None:
            return self
        return self.model_copy(update=extra.model_dump(exclude_unset=True))


class AudioDraws(BaseModel, frozen=True):
    volume: float | None
    pitch: float | None
    phaser_ms: float | None
    treble_db: float | None
    bass_cut_db: float | None
    mid_db: float | None
    echo: str | None
    echo_ms: float | None
    echo_decay: float | None
    loud: str | None
    loud_value: float | None
    compress_db: float | None
    deess_db: float | None
    limit: float | None


def _draw(s: AudioSettings, rng: random.Random) -> AudioDraws:
    echo = pick(s.echo, rng) or (
        "reverb" if s.reverb_ms else "room" if s.room_ms else None
    )
    loud = pick(s.loud, rng)
    phaser = draw(s.phaser_shift, rng)
    return AudioDraws(
        volume=draw(s.volume, rng),
        pitch=draw(s.pitch, rng),
        phaser_ms=(
            min(
                _PHASER_DELAY_MS[1],
                max(_PHASER_DELAY_MS[0], phaser * _PHASER_DELAY_PER_UNIT),
            )
            if phaser
            else None
        ),
        treble_db=draw(s.treble_db, rng),
        bass_cut_db=draw(s.bass_cut_db, rng),
        mid_db=draw(s.mid_db, rng),
        echo=echo,
        echo_ms=draw(s.reverb_ms if echo == "reverb" else s.room_ms, rng),
        echo_decay=draw(s.reverb_decay, rng),
        loud=loud if loud in _LOUD_RANGE else None,
        loud_value=rng.uniform(*_LOUD_RANGE[loud]) if loud in _LOUD_RANGE else None,
        compress_db=draw(s.compress_db, rng),
        deess_db=draw(s.deess_db, rng),
        limit=draw(s.limit, rng),
    )


class AudioTransformer:
    """One copy's sound: drawn on creation, then `filters()`."""

    def __init__(
        self,
        settings: AudioSettings,
        speed: float,
        sample_rate: int,
        rng: random.Random,
    ) -> None:
        self.settings = settings
        self.speed = speed
        self.sample_rate = sample_rate
        self.draws = _draw(settings, rng)

    def filters(self, makeup_db: float = 0.0) -> str:
        """The -filter:a chain; `makeup_db` goes in before the limiter."""
        d = self.draws
        # Start at 0, as the video does (`VideoTransformer.filters`).
        chain = ["asetpts=PTS-STARTPTS", *self._pitch_and_tempo()]
        if d.volume is not None and d.volume != 1:
            chain.append(f"volume={d.volume:.4f}")
        if d.phaser_ms:
            chain.append(f"aphaser=delay={d.phaser_ms:.3f}:decay=0.15:speed=0.3")
        if d.treble_db:
            chain.append(f"equalizer=f=8000:t=h:w=2000:g={d.treble_db:.2f}")
        if d.bass_cut_db:
            chain.append(f"equalizer=f=150:t=h:w=100:g={-d.bass_cut_db:.2f}")
        chain += self._echo()
        if d.mid_db:
            chain.append(f"equalizer=f=1000:t=q:w=1:g={d.mid_db:.2f}")
        chain += self._loud()
        if d.compress_db is not None:
            chain.append(
                f"acompressor=threshold={d.compress_db:.1f}dB:ratio=4:attack=5:release=50"
            )
        if d.deess_db:
            chain.append(f"equalizer=f=7000:t=h:w=3000:g={-d.deess_db:.2f}")
        if makeup_db > 0:
            chain.append(f"volume={makeup_db:.2f}dB")
        if d.limit is not None:
            chain.append(f"alimiter=limit={d.limit:.3f}:attack=5:release=50")
        return ",".join(chain)

    def _pitch_and_tempo(self) -> list[str]:
        """Played at a higher/lower rate then resampled back moves pitch and
        speed together, so atempo puts the speed back."""
        chain = []
        tempo = self.speed
        pitch = self.draws.pitch
        if pitch is not None and abs(pitch) >= _PITCH_MIN:
            rate = round(self.sample_rate * 2 ** (pitch / 12))
            chain += [
                f"aresample={self.sample_rate}",
                f"asetrate={rate}",
                f"aresample={self.sample_rate}",
            ]
            tempo = self.speed * self.sample_rate / rate
        return [*chain, f"atempo={tempo:.6g}"]

    def _echo(self) -> list[str]:
        d = self.draws
        if not d.echo_ms:
            return []
        if d.echo == "reverb":
            return [f"aecho=0.8:0.88:{d.echo_ms:.0f}:{d.echo_decay or 0.4:.2f}"]
        return [
            f"aecho=0.8:0.9:{d.echo_ms:.0f}|{d.echo_ms * _ROOM_ECHO_SECOND_TAP:.0f}:0.3|0.2"
        ]

    def _loud(self) -> list[str]:
        d = self.draws
        if d.loud is None or d.loud_value is None:
            return []
        v, k = d.loud_value, self.settings.loudness
        effect = {
            "chorus": f"chorus=0.5:0.85:{v:.0f}|{v + 5:.0f}:{0.2 * k:.3f}|{0.15 * k:.3f}:0.25|0.3:1|0.75",
            # ffmpeg's flanger depth tops out at 10 (loudness > 1 went past it).
            "flanger": f"flanger=delay={v:.1f}:depth={min(min(v, 10) * k, 10):.2f}:width=25:speed=0.5",
            "phaser": f"aphaser=in_gain=0.4:out_gain=0.74:delay={v:.1f}:decay={0.2 * k:.3f}:speed=0.5",
            "tremolo": f"tremolo=f={v:.1f}:d={0.15 * k:.3f}",
            "vibrato": f"vibrato=f={v:.1f}:d={0.12 * k:.3f}",
        }
        return [effect[d.loud]]


def room_tone_input(seconds: float, sample_rate: int) -> list[str]:
    """A faint pink-noise track for a silent source (a phone always records
    sound): one more ffmpeg input."""
    source = f"anoisesrc=color=pink:amplitude=0.002:sample_rate={sample_rate}"
    return ["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", source]
