"""
AI generation tools. Every module here follows the same five-endpoint shape
(see routers/task_helpers.py): POST /run creates a job and returns
{"job_id": ...} immediately, GET /jobs/{id} renders the status page, GET
/jobs/{id}/status is the polling payload, GET /files/{id}/{index} streams a
result file. `index.py` is the unified picker page that fronts them all.

Adding a generation backend = one module in this shape + one line in
routers/__init__.py's ROUTERS.
"""
