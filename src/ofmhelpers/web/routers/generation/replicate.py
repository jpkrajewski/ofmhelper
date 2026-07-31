"""
ofmhelpers/web/routers/generation/replicate.py

The "Replicate" page: paste a reel link (or upload the file) and nothing
else. The reel_machine module downloads it, hands the video plus one fixed
analysis prompt to Gemini, and validates the Seedance 2.0 prompt JSON that
comes back. The review page plays the source video next to that JSON in an
editable textarea, so you can tweak it before dropping in your character's
reference images and firing the real generation through KieAIClient (same as
web/routers/generation/seedance.py).

There are no shape/look/gender/persona/provider inputs: everything that used
to be assembled locally now comes out of the model's own reading of the
video (see reel_machine/CLAUDE.md). The provider is an env-var deployment
choice (`REEL_MACHINE_LLM_PROVIDER`), not a per-job form field.

Two job types share this router, dispatched by `job["task"]`:
  - "replicate_intake" -> download + analyze (Stage 1), rendered by the
    dedicated replicate_review.html template.
  - "replicate" -> the final Seedance video generation (Stage 2), rendered
    by the same job_status.html template every other generation tool uses.
"""

import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from ofmhelpers.downloaders.generic import is_video_file
from ofmhelpers.reel_machine import generation, pipeline
from ofmhelpers.web.auth import get_elevenlabs_api_key, get_kie_api_key
from ofmhelpers.web.queue import enqueue
from ofmhelpers.web.routers.helpers.elevenlabs import VOICES as ELEVENLABS_VOICES
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    asset_card,
    build_ordered_paths,
    job_inputs,
    job_status_payload,
    make_job_dir,
    save_upload,
    serve_job_file,
)
from ofmhelpers.web.stores.jobs import create_job, get_job, list_jobs, run_job
from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/replicate", tags=["replicate"])

# Where a job's downloaded reel lives -- separate from ASSETS_ROOT
# (uploads/assets), which is only for reusable reference files.
INTAKE_ROOT = Path("uploads") / "replicate_intake"


def _latest_child_job(source_job_id: str, task: str) -> dict | None:
    """Most recent job of `task` (e.g. "replicate" or "elevenlabs") that was
    generated *from* this intake job -- so the review page can re-render its
    own last result on reload instead of a page refresh losing it (results
    otherwise only ever existed in the client-side generation.js card).
    list_jobs() is newest-first, so the first match is the latest run."""
    for job in list_jobs():
        if (
            job["task"] == task
            and (job.get("params") or {}).get("source_job_id") == source_job_id
        ):
            return job
    return None


def _downloaded_video(job: dict) -> Path | None:
    """The reel this intake job already pulled down, if it is still on disk.

    A rerun must not re-download: the usual failure is the analysis call, not
    the fetch, and the links that are hard to download once (Instagram) are
    exactly the ones that won't hand the file over a second time. A done job
    records the path in its result; a failed one never got that far, so its
    work dir is scanned instead."""
    result = job.get("result")
    if isinstance(result, dict) and result.get("video_path"):
        path = Path(result["video_path"])
        if path.is_file():
            return path
    work_dir = INTAKE_ROOT / job["id"]
    if not work_dir.is_dir():
        return None
    videos = sorted(p for p in work_dir.rglob("*") if p.is_file() and is_video_file(p))
    return videos[0] if videos else None


def _prefill(job_id: str) -> dict | None:
    """`/replicate?from=<job_id>` re-opens the form with a past intake's
    context note already typed in and its downloaded reel reusable, so a job
    that died in analysis is one click from running again."""
    job = get_job(job_id)
    if job is None or job["task"] != "replicate_intake":
        return None
    video = _downloaded_video(job)
    params = job.get("params") or {}
    return {
        "job_id": job_id,
        "context": params.get("context", ""),
        "source": params.get("source", ""),
        "video_name": video.name if video else "",
        "error": job.get("error"),
    }


# The two hunts the VA does by hand between analysis and generation, pre-typed
# off the analysis so the niche doesn't have to be retyped every reel:
#   - wardrobe: find an outfit that fits the niche, turn it into a clothes sheet
#   - similar reels: find more of this format to clone next
_OUTFIT_ENGINES = (
    ("Pinterest", "https://www.pinterest.com/search/pins/?q={q}"),
    ("Google Images", "https://www.google.com/search?tbm=isch&q={q}"),
)
_REEL_ENGINES = (
    ("TikTok", "https://www.tiktok.com/search?q={q}"),
    # Instagram's own keyword search and hashtag pages are login-gated (they
    # stopped serving those logged out in 2024), which is why searching
    # Instagram from here was useless. Its reels ARE indexed publicly, so a
    # site: search reaches them without a login wall; the niche pages proper
    # are instagram.com/popular/<slug>, built from hunt.instagram_topics.
    (
        "Instagram via Google",
        "https://www.google.com/search?q=site%3Ainstagram.com%2Freel%2F+{q}",
    ),
)
# The niche pages: /popular/<slug>, e.g. /popular/baseball-girl, built from
# hunt.instagram_topics. Unlike /explore/tags/ and Instagram's own search
# these are not login-gated. Bare /popular/ is deliberately NOT linked -- with
# no slug it is a generic signed-out landing page, one more button to ignore.
_INSTAGRAM_TOPIC_URL = "https://www.instagram.com/popular/{slug}/"
_MAX_QUERY_CHARS = 120
_MIN_QUERY_CHARS = 3


def _searches(queries: list[str], engines: tuple[tuple[str, str], ...]) -> list[dict]:
    """Each usable query paired with one link per engine. Blank, too-short and
    duplicate queries drop out, so a half-filled analysis yields fewer rows
    rather than dead searches."""
    searches = []
    seen = set()
    for raw in queries:
        query = " ".join(raw.split())[:_MAX_QUERY_CHARS].strip()
        if len(query) < _MIN_QUERY_CHARS or query.lower() in seen:
            continue
        seen.add(query.lower())
        searches.append(
            {
                "query": query,
                "links": [
                    {"label": label, "url": url.format(q=quote_plus(query))}
                    for label, url in engines
                ],
            }
        )
    return searches


def _as_womens_outfit(query: str) -> str:
    """Pinterest and Google Images answer a bare clothing description with
    menswear and flat-lays as often as not; the reels being cloned are always
    a girl, so the query says so."""
    if not query.strip() or any(
        word in query.lower() for word in ("girl", "woman", "women")
    ):
        return query
    return f"girl {query}"


def _subject(prompt: dict) -> dict:
    """The person being cloned. Mirrors ReelAnalysis.subject: the entry with
    id "subject", else the first one. Everyone else in `people` is a cameraman
    or a passer-by, and their "wardrobe: not visible" is not something to go
    shopping for."""
    people = [p for p in (prompt.get("people") or []) if isinstance(p, dict)]
    for person in people:
        if person.get("id") == "subject":
            return person
    return people[0] if people else {}


def _outfit_searches(prompt: dict | None, hunt: dict | None = None) -> list[dict]:
    """Outfit searches for the main subject, best terms first: the free
    model's alternatives (see reel_machine/hunt.py) ahead of the ones derived
    straight from the analysis' setting and the subject's own wardrobe. The
    derived ones stay as the floor -- there is no key on every deployment, and
    old jobs have no hunt stored at all."""
    if not prompt:
        return []
    environment = (prompt.get("environment") or "").strip()
    return _searches(
        [
            _as_womens_outfit(idea)
            for idea in [
                *((hunt or {}).get("outfit_ideas") or []),
                f"{environment} outfit inspo" if environment else "",
                _subject(prompt).get("wardrobe") or "",
            ]
        ],
        _OUTFIT_ENGINES,
    )


def _reel_searches(prompt: dict | None, hunt: dict | None = None) -> list[dict]:
    """Searches for more reels like this one -- the free model's phrases first,
    then the ones derived from the analysis itself."""
    if not prompt:
        return []
    environment = (prompt.get("environment") or "").strip()
    return _searches(
        [
            *((hunt or {}).get("search_queries") or []),
            (prompt.get("context") or "").strip(),
            (prompt.get("viral_factor") or "").strip(),
            f"{environment} reel" if environment else "",
        ],
        _REEL_ENGINES,
    )


def _instagram_topic_links(hunt: dict | None) -> list[dict]:
    """`instagram.com/popular/<slug>` pages for this reel's niche. Only the
    free model produces these: a topic slug is 1-3 words someone would
    actually name the niche ("baseball-girl"), which is not something the
    analysis' prose can be sliced into."""
    return [
        {"label": f"/popular/{slug}", "url": _INSTAGRAM_TOPIC_URL.format(slug=slug)}
        for slug in ((hunt or {}).get("instagram_topics") or [])
        if slug
    ]


def _run_replicate_intake(source: str, work_dir: str, context: str = "") -> dict:
    analysis = pipeline.analyze(source, Path(work_dir), context=context)
    # Both shapes of the prompt are stored on purpose: `prompt` is the
    # validated object (what the Action log records, and proof the response
    # passed schema.parse_analysis), `prompt_text` is the exact string the
    # review textarea shows and Seedance is given. A response that failed
    # validation still lands here -- `prompt` is null, `analysis_error` says
    # why, and `prompt_text` is the provider's raw answer for the VA to fix.
    return {
        "video_path": str(analysis.video_path),
        "duration": analysis.duration,
        "provider": analysis.provider,
        "prompt": analysis.prompt.model_dump() if analysis.prompt else None,
        "prompt_text": analysis.prompt_text,
        "speech": analysis.speech,
        "analysis_error": analysis.error,
        # Second-pass search terms (reel_machine/hunt.py). Stored with the job
        # so reopening it doesn't re-ask the model; empty dict on old jobs and
        # whenever that call was skipped or failed.
        "hunt": analysis.hunt.to_dict(),
    }


def _drop_nulls(value):
    """Strip every null out of the prompt, at any depth.

    The analysis prompt asks for nulls as a deliberate signal to *itself* --
    "line: null if no dialogue", "pose: null if off camera" -- so a fully
    filled-in answer still carries a null on most scene_events entries. Seedance
    reads the prompt as instructions, not as a schema, and an absent key says
    exactly what a null one does while costing nothing to read.

    Only nulls go. An empty string is a real (if unhelpful) answer and stays."""
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value if v is not None]
    return value


def _minify_prompt_json(script: str) -> str:
    """Seedance is handed this string verbatim, and a <textarea> submits its
    value with every newline normalized to CRLF (HTML spec) -- so the
    pretty-printed JSON the review page shows arrived as a blob full of \\r\\n
    and went straight to the provider that way. Re-serialize it compactly
    instead: one line, no indentation, no carriage returns, no nulls.

    Text that isn't JSON falls through with newlines normalized rather than
    being rejected: a VA fixing an unvalidated raw answer by hand still has to
    be able to submit it."""
    try:
        data = json.loads(script)
    except json.JSONDecodeError:
        return script.replace("\r\n", "\n").replace("\r", "\n")
    return json.dumps(_drop_nulls(data), separators=(",", ":"), ensure_ascii=False)


def _run_replicate_generate(
    api_key: str,
    prompt: str,
    duration: int,
    resolution: str,
    character_ref_paths: list[str],
    video_ref_paths: list[str] | None = None,
    audio_ref_paths: list[str] | None = None,
) -> list[dict]:
    out_path = generation.generate_reel_clone(
        api_key=api_key,
        prompt=prompt,
        character_ref_paths=character_ref_paths,
        video_ref_paths=video_ref_paths,
        audio_ref_paths=audio_ref_paths,
        duration=duration,
        resolution=resolution,
    )
    return [{"name": out_path.name, "path": str(out_path)}]


def _intake_row(job: dict) -> dict:
    """One row of the "past analyses" list on /replicate. `valid` is whether
    the analysis passed schema validation -- a done job whose prompt is null
    still opens, it just needs the JSON fixed by hand first."""
    result = job.get("result") or {}
    return {
        "id": job["id"],
        "status": job["status"],
        "source": (job.get("params") or {}).get("source", ""),
        "context": (job.get("params") or {}).get("context", ""),
        "error": job.get("error"),
        "valid": bool(result.get("prompt")) if isinstance(result, dict) else False,
        "created_at_display": datetime.fromtimestamp(job["created_at"], UTC).strftime(
            "%Y-%m-%d %H:%M"
        ),
    }


@router.get("")
def form(request: Request, from_job: Annotated[str, Query(alias="from")] = ""):
    intakes = [_intake_row(j) for j in list_jobs() if j["task"] == "replicate_intake"]
    return templates.TemplateResponse(
        request,
        "replicate_form.html",
        {"intakes": intakes, "prefill": _prefill(from_job) if from_job else None},
    )


@router.post("/intake")
async def intake(
    request: Request,
    source_url: Annotated[str, Form()] = "",
    source_file: Annotated[UploadFile | None, File()] = None,
    context: Annotated[str, Form()] = "",
    reuse_job_id: Annotated[str, Form()] = "",
):
    source = source_url.strip()
    if source_file is not None and source_file.filename:
        upload_dir = make_job_dir(INTAKE_ROOT)
        source = save_upload(upload_dir, source_file)
    if not source and reuse_job_id:
        # Rerun of a past intake: reuse the file it already downloaded, and
        # only fall back to re-fetching the original link if that file is gone.
        prior = get_job(reuse_job_id)
        if prior is not None and prior["task"] == "replicate_intake":
            video = _downloaded_video(prior)
            source = (
                str(video) if video else (prior.get("params") or {}).get("source", "")
            )
    if not source:
        raise HTTPException(
            status_code=400, detail="Provide a reel URL or upload a file"
        )

    # Stored in params, not just passed to the worker, so the Action log shows
    # which note produced which analysis.
    params = {"source": source, "context": context.strip()}
    job_id = create_job("replicate_intake", params, actor=request.session.get("role"))
    enqueue(
        run_job,
        job_id,
        _run_replicate_intake,
        {**params, "work_dir": str(INTAKE_ROOT / job_id)},
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["task"] == "replicate_intake":
        video_job = _latest_child_job(job_id, "replicate")
        voice_job = _latest_child_job(job_id, "elevenlabs")
        video_assets = (
            [
                asset_card(
                    f["name"],
                    idx,
                    f"/replicate/files/{video_job['id']}",
                    source="Seedance video",
                )
                for idx, f in enumerate(video_job["result"])
            ]
            if video_job and video_job.get("status") == "done"
            else []
        )
        voice_assets = (
            [
                asset_card(
                    f["name"],
                    idx,
                    f"/helpers/elevenlabs/files/{voice_job['id']}",
                    source="ElevenLabs voice",
                )
                for idx, f in enumerate(voice_job["result"])
            ]
            if voice_job and voice_job.get("status") == "done"
            else []
        )
        result = job.get("result") or {}
        prompt = result.get("prompt")
        hunt = result.get("hunt")
        return templates.TemplateResponse(
            request,
            "replicate_review.html",
            {
                "job": job,
                "kie_api_key": get_kie_api_key(request),
                # The review page can send the subject's speech straight to
                # /helpers/elevenlabs/run, so it needs that tool's voices.
                "voices": list(ELEVENLABS_VOICES),
                "elevenlabs_api_key": get_elevenlabs_api_key(),
                # Re-renders the last video/voice generation made from this
                # intake job, so a page reload doesn't lose a result that
                # only ever existed in a client-side generation.js card.
                "video_job": video_job,
                "voice_job": voice_job,
                "video_assets": video_assets,
                "voice_assets": voice_assets,
                # Pre-typed searches for the two manual steps around the
                # generation: finding an outfit in this reel's niche, and
                # finding more reels like it to clone next. `hunt` is the free
                # model's second pass over Gemini's analysis (hunt.py).
                "outfit_searches": _outfit_searches(prompt, hunt),
                "reel_searches": _reel_searches(prompt, hunt),
                "reel_topic_links": _instagram_topic_links(hunt),
            },
        )

    assets = []
    if job.get("status") == "done":
        assets = [
            asset_card(f["name"], idx, f"/replicate/files/{job_id}")
            for idx, f in enumerate(job["result"])
        ]
    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "job_inputs": job_inputs(job),
            "title": "Replicate",
            "pending_message": "Generating video… this can take a few minutes.",
            "back_url": "/replicate",
            "back_label": "clone another reel",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["task"] == "replicate_intake":
        return {
            "job_id": job["id"],
            "task": job["task"],
            "status": job["status"],
            "error": job.get("error"),
        }
    return job_status_payload(job, "/replicate/files")


@router.get("/video/{job_id}")
def source_video(job_id: str):
    """Streams the downloaded source reel back for the review page's <video>
    player -- the analysis is all about motion and timing, so reviewing it
    against a grid of stills was never good enough."""
    job = get_job(job_id)
    if (
        job is None
        or job.get("task") != "replicate_intake"
        or job.get("status") != "done"
    ):
        raise HTTPException(status_code=404, detail="Source video not available")

    path = Path(job["result"]["video_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Source video no longer exists")
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    return FileResponse(path, media_type=media_type)


@router.post("/generate")
async def generate(
    request: Request,
    api_key: Annotated[str, Form()],
    script: Annotated[str, Form()],
    duration: Annotated[int, Form()] = 15,
    resolution: Annotated[str, Form()] = "720p",
    character_images: Annotated[list[UploadFile] | None, File()] = None,
    character_images_manifest: Annotated[str, Form()] = "[]",
    character_videos: Annotated[list[UploadFile] | None, File()] = None,
    character_videos_manifest: Annotated[str, Form()] = "[]",
    character_audio: Annotated[list[UploadFile] | None, File()] = None,
    character_audio_manifest: Annotated[str, Form()] = "[]",
    source_job_id: Annotated[str, Form()] = "",
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    if not script.strip():
        raise HTTPException(status_code=400, detail="Script is required")

    character_ref_paths = build_ordered_paths(
        character_images_manifest, character_images or [], ASSETS_ROOT
    )
    video_ref_paths = build_ordered_paths(
        character_videos_manifest, character_videos or [], ASSETS_ROOT
    )
    audio_ref_paths = build_ordered_paths(
        character_audio_manifest, character_audio or [], ASSETS_ROOT
    )

    params = {
        "prompt": _minify_prompt_json(script),
        "duration": duration,
        "resolution": resolution,
    }
    stored_params = {
        **params,
        "character_images": character_ref_paths,
        "character_videos": video_ref_paths,
        "character_audio": audio_ref_paths,
    }
    if source_job_id:
        # Lets the review page find and re-render its own last generation on
        # reload (see _latest_child_job) -- not used by the generation call.
        stored_params["source_job_id"] = source_job_id
    job_id = create_job(
        "replicate",
        stored_params,
        actor=request.session.get("role"),
    )
    enqueue(
        run_job,
        job_id,
        _run_replicate_generate,
        {
            "api_key": api_key,
            **params,
            "character_ref_paths": character_ref_paths,
            "video_ref_paths": video_ref_paths,
            "audio_ref_paths": audio_ref_paths,
        },
    )
    return {"job_id": job_id}


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: Annotated[int, Query()] = 0):
    job = get_job(job_id)
    return serve_job_file(job, index, as_attachment=bool(dl))
