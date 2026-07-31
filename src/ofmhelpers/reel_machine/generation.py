"""
Fires the actual Seedance 2.0 generation via the *existing* KieAIClient --
reel_machine has no HTTP client of its own; this only uploads the user's
character reference images and calls generate_video_seedance2, same as
web/routers/generation/seedance.py does.
"""

from pathlib import Path

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient


def generate_reel_clone(
    api_key: str,
    prompt: str,
    character_ref_paths: list[str],
    duration: int = 15,
    resolution: str = "720p",
    model: str = "bytedance/seedance-2",
    video_ref_paths: list[str] | None = None,
    audio_ref_paths: list[str] | None = None,
) -> Path:
    """Reference videos and audio are optional, and passed as `None` rather
    than `[]` when unused: generate_video_seedance2 only puts a
    reference_*_urls key in the payload for the lists that are non-empty."""
    client = KieAIClient.from_env(api_key=api_key)
    reference_image_urls = [client.upload_local_file(p) for p in character_ref_paths]
    reference_video_urls = [client.upload_local_file(p) for p in video_ref_paths or []]
    reference_audio_urls = [client.upload_local_file(p) for p in audio_ref_paths or []]
    return client.generate_video_seedance2(
        prompt=prompt,
        model=model,
        resolution=resolution,
        aspect_ratio="9:16",  # reels are vertical, unlike seedance.py's 16:9 default
        duration=duration,
        generate_audio=True,
        reference_image_urls=reference_image_urls,
        reference_video_urls=reference_video_urls or None,
        reference_audio_urls=reference_audio_urls or None,
    )
