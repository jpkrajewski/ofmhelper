"""
Lens/camera-look presets for Seedance prompt packages.

Ported from the old reel-machine skill bundle's looks.md reference (a
downloaded Bash/Markdown product template, now retired). The lens is HALF
of a cloned reel's look -- pick one per clip to match the reference reel's
camera character (vignette, fisheye strength, camera height, who holds it).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Look:
    name: str
    when: str
    prompt: str
    negative: str


LOOKS: dict[str, Look] = {
    "gopro_pov": Look(
        name="GoPro POV (porthole)",
        when="POV duets, street encounters, prank/reveal reels -- the other person holds the cam.",
        prompt=(
            "shot on an ULTRA-WIDE FISHEYE action-cam lens (GoPro-style): STRONG barrel "
            "distortion across the whole image, framed by a DARK ROUNDED VIGNETTE -- thick "
            "curved black corners eating deep into the frame, almost a circular porthole "
            "look. Straight lines visibly BOW outward; the subject bulges slightly toward "
            "the center of the lens. The camera is held CLOSE and slightly BELOW eye level, "
            "looking up -- very intimate; the subject fills the frame from the hips up, "
            "face near the center where the lens is sharpest"
        ),
        negative=(
            "flat rectangular frame, no vignette, no fisheye, straight power lines, "
            "telephoto compression, the subject small in frame"
        ),
    ),
    "phone_selfie": Look(
        name="Phone front-cam selfie",
        when="solo monologues, selfie duets, CTA talking-heads -- she holds the phone.",
        prompt=(
            "filmed on a phone FRONT camera at arm's length: mild wide-angle selfie "
            "distortion (face slightly rounded at selfie distance), natural phone exposure, "
            "mild grain, face sharp while the background stays slightly soft. NO vignette, "
            "normal full rectangular frame"
        ),
        negative=(
            "fisheye porthole, dark rounded corners, DSLR shallow depth of field, "
            "beauty-filter skin"
        ),
    ),
    "dv_camcorder": Look(
        name="DV camcorder (retro Y2K)",
        when="nostalgic day-in-my-life, daily-vlog, artsy character reels.",
        prompt=(
            "shot like a late-90s DV camcorder tape: slightly soft focus, muted colors with "
            "a warm magenta cast, mild analog noise, gentle motion smear on movement, "
            "slight interlace shimmer, handheld amateur framing with imperfect horizon"
        ),
        negative="crisp digital sharpness, modern HDR colors, clean noise-free image",
    ),
    "webcam": Look(
        name="Webcam / laptop",
        when='desk reels, reaction-style skits, "my viewers asked" formats.',
        prompt=(
            "framed like a laptop WEBCAM: fixed camera slightly BELOW eye level on the "
            "desk, sitting close to the lens, slightly compressed webcam colors, mild "
            "sensor noise in the shadows, flat even room lighting from the screen glow, "
            "static framing (the camera never moves)"
        ),
        negative="handheld wobble, camera movement, cinematic lighting",
    ),
    "cctv": Look(
        name="CCTV / high-angle",
        when='viral "caught on camera" formats, no-dialogue action beats.',
        prompt=(
            "framed like a ceiling SECURITY camera: high corner angle looking down, very "
            "wide lens with mild fisheye, desaturated cool tones, slight noise, static "
            "mount (no handheld motion), the scene plays out below"
        ),
        negative="eye-level framing, warm grading, camera following the subject",
    ),
    "third_person": Look(
        name="Third-person phone (friend films)",
        when="get-ready/outfit checks, walk-and-talk with a friend behind the camera.",
        prompt=(
            "filmed by a friend on a phone REAR camera from a few steps away: normal focal "
            "length (no fisheye, no selfie distortion), handheld micro-shake and small "
            "reframes, natural phone HDR exposure -- both hands free, no phone held"
        ),
        negative="selfie arm, fisheye, vignette, gimbal smoothness",
    ),
}
