import re
import subprocess
from enum import StrEnum
from functools import lru_cache

from pydantic import BaseModel, ConfigDict


class CloneLinksTask(StrEnum):
    RUN = "clone_links"
    REVIEW = "clone_links_review"


class CloneLinksConstants(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str = "Clone links"
    prefix: str = "/clone-links"
    files_prefix: str = "/clone-links/files"
    thumbs_prefix: str = "/clone-links/thumb"
    batch_pattern: str = r"^[0-9a-f]{8,32}$"
    sent_ttl_s: int = 24 * 3600
    not_queued: str = "Not queued: run it again"
    run_key: str = "clone-links:run:{actor}:{batch}"
    stop_key: str = "clone-links:stop:{batch}"
    halt_key: str = "clone-links:halt:{batch}"
    requeued_key: str = "clone-links:requeued:{review_id}"
    queue_failed_job: str = "Could not queue the AI check"

    token: re.Pattern[str] = re.compile(r"[^\s,;<>\"']+")
    glued: re.Pattern[str] = re.compile(r"(?<!=)(?=https?://)", re.IGNORECASE)
    trailing: str = ".!?:)]}"
    tld: re.Pattern[str] = re.compile(r"\.[a-z]{2,}$")
    not_a_video_site: str = "Please put Instagram, TikTok, Twitter etc."
    not_one_post: str = "Paste a reel's link, not a profile or playlist"
    share_link: str = "Open the link and copy the reel's address"
    instagram_post: re.Pattern[str] = re.compile(
        r"^/(?!share/)(?:[\w.]+/)?(?:reels?(?!/audio/)|p|tv)/([\w-]+)"
    )
    tiktok_video: re.Pattern[str] = re.compile(r"^/@[\w.-]+/video/\d+")
    instagram_domain: str = "instagram.com"
    tiktok_domain: str = "tiktok.com"
    share_hosts: tuple[str, ...] = ("instagram.com", "facebook.com")
    site_names: dict[str, str] = {
        "tiktok.com": "TikTok",
        "youtube.com": "YouTube",
        "youtu.be": "YouTube",
    }

    thumb_key: re.Pattern[str] = re.compile(r"^[0-9a-f]{24}$")
    cannot_open: str = "Can't open this video"
    too_slow: str = "Took too long, try again"
    login_words: tuple[str, ...] = (
        "login",
        "log in",
        "sign in",
        "cookies",
        "no longer valid",
    )
    auth_refused_words: tuple[str, ...] = ("http error 400", "http error 401")
    throttled_words: tuple[str, ...] = (
        "http error 429",
        "too many requests",
        "rate-limit",
        "rate limit",
    )
    no_answer_words: tuple[str, ...] = (
        "timed out",
        "connection",
        "temporarily unavailable",
    )

    target_label: str = "Wan 3.0"
    stopped: str = "Stopped"
    run_gone: str = "This run no longer exists"
    could_not_download: str = "Could not download this reel"
    needs_login: str = "Needs login: upload your cookies on the Cookies page"
    no_video: str = "No video at this link"
    not_kept: str = "The video could not be kept"
    run_unreadable: str = "The video cannot be read"
    refetch: str = "Still generating: this reel is downloaded again on the next try"
    run_not_saved: str = "Still generating: the video is saved on the next try"
    login_wall: re.Pattern[str] = re.compile(
        r"\blog ?in\b|\bsign in\b|cookies", re.IGNORECASE
    )

    rules: str = """\
You check short AI-generated videos (Instagram reels) before they are posted.
You get a contact sheet of one whole video: stills from its first frame to its
last, in time order (left to right, top to bottom), each labelled with its time.

Look for anything that gives the video away as AI-made or broken:
- faces melting, warping or turning into someone else
- bodies: extra or missing limbs, fingers or heads; joints bending the wrong
  way; parts merging; missing features (no belly button, no nipples)
- clothes: changing, morphing, appearing or vanishing between stills; fabric
  fused with skin; clothes that make no sense on this person (a man in a
  strapless tube top, a top missing the straps it needs, one sleeve only)
- garbled or nonsense text and logos on clothes or objects
- objects or background appearing out of nowhere, smeared or dissolving

Ordinary motion blur, lighting changes, camera moves and cuts are fine.

List every problem. Mark one "blocking" when it alone would show a viewer
the reel is AI-made or broken, "minor" when it is easy to miss.
The percent is how sure you are the whole reel should not be posted: any
blocking problem makes it high, and several minor ones can add up to high.
Keep it short: a few words per problem, one sentence of reasoning."""
    mirrored: str = (
        " The video was mirrored left to right on purpose: text and logos reading"
        " backwards are not a glitch."
    )
    unreadable: str = "Could not read the video"
    no_sheet: str = "Could not make the contact sheet"
    too_long: str = "AI check took too long"
    no_answer: str = "No AI model answered"
    no_model: str = "No AI model set up to check videos"
    broken_answer: str = "The AI gave a broken answer"
    refused: str = "The AI refused to check this video"
    key_rejected: str = "The AI key was rejected"
    cannot_take_video: str = "The AI could not take this video"
    check_failed: str = "The AI check failed"
    read_errors: tuple[type[Exception], ...] = (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    )


@lru_cache(maxsize=1)
def clone_links_constants() -> CloneLinksConstants:
    return CloneLinksConstants()
