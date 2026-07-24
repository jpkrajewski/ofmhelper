"""
Fires the actual Seedance 2.0 generation via the *existing* KieAIClient --
reel_machine has no HTTP client of its own; this only uploads the user's
character reference images and calls generate_video_seedance2, same as
web/routers/seedance.py does.
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
) -> Path:
    client = KieAIClient.from_env(api_key=api_key)
    reference_image_urls = [client.upload_local_file(p) for p in character_ref_paths]
    return client.generate_video_seedance2(
        prompt=prompt,
        model=model,
        resolution=resolution,
        aspect_ratio="9:16",  # reels are vertical, unlike seedance.py's 16:9 default
        duration=duration,
        generate_audio=True,
        reference_image_urls=reference_image_urls,
    )
