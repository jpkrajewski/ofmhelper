"""
AI generation. `ai.py` is the one generation router: every model of every
provider (`aigenproviders/`) posts to `/ai/run` and its jobs share one task
name, so adding a model or a provider never adds a module here. `index.py` is
its /generate page, `clone.py` is /clone (one generation per source video,
through `ai.run_ai`), and `legacy.py` keeps the job pages of the per-model
routers `ai.py` replaced.
"""
