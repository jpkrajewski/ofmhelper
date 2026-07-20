import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, FileResponse

from elevenlabs.client import ElevenLabs

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs"])

OUTPUT_ROOT = Path("uploads") / "elevenlabs-out"

VOICES = {
    "Belu": "xqn9Hx2XbhMayvjlX5YD",
    "Chad": "eadgjmk4R4uojdsheG9t",
    "George": "JBFqnCBsd6RMkjVDRZzb",
}
"sk_96eabbd4c2d5f03d686056cb47477883058f12abe9a65f29"


def _run_tts(
    api_key: str,
    text: str,
    voice: str,
    model_id: str,
    output_format: str,
    job_dir: str,
) -> list[dict]:
    client = ElevenLabs(api_key=api_key)

    voice_id = VOICES.get(voice, voice)  # allow raw voice_id passthrough too

    audio = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        output_format=output_format,
    )

    out_path = Path(job_dir) / f"{uuid.uuid4().hex[:8]}.mp3"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("wb") as f:
        for chunk in audio:
            f.write(chunk)

    return [{"name": out_path.name, "path": str(out_path)}]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(
        request,
        "elevenlabs_form.html",
        {"voices": list(VOICES.keys())},
    )


@router.post("/run")
async def run(
    background_tasks: BackgroundTasks,
    api_key: str = Form(...),
    text: str = Form(...),
    voice: str = Form("George"),
    model_id: str = Form("eleven_v3"),
    output_format: str = Form("mp3_44100_128"),
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    job_dir = OUTPUT_ROOT / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)

    # api_key is intentionally excluded from stored job params -- passed only
    # to the background task, never persisted via create_job/get_job.
    params = {
        "text": text,
        "voice": voice,
        "model_id": model_id,
        "output_format": output_format,
    }
    job_id = create_job("elevenlabs", params)
    background_tasks.add_task(
        run_job,
        job_id,
        _run_tts,
        {
            "api_key": api_key,
            **params,
            "job_dir": str(job_dir),
        },
    )

    return RedirectResponse(url=f"/elevenlabs/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "elevenlabs_form.html", {}, status_code=404
        )

    if job.get("status") == "done":
        for idx, f in enumerate(job["result"]):
            f["index"] = idx

    return templates.TemplateResponse(request, "elevenlabs_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    job = get_job(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    files = job["result"]
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(files[index]["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    return FileResponse(path, filename=path.name, media_type="audio/mpeg")
