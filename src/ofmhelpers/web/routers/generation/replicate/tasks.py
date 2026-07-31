"""The two background job bodies: Stage 1 (download + analyze) and Stage 2
(the real Seedance generation). Both run on the RQ worker, so nothing here
may touch a request."""

from pathlib import Path

from ofmhelpers.reel_machine import generation, pipeline


def _run_replicate_intake(source: str, work_dir: str, context: str = "") -> dict:
    analysis = pipeline.analyze(source, Path(work_dir), context=context)
    # Both shapes of the prompt are stored on purpose: `prompt` is the
    # validated object (what the Action log records, and proof the response
    # passed ReelAnalysis.from_llm_text), `prompt_text` is the exact string the
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
        "hunt": analysis.hunt.model_dump(),
    }


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
