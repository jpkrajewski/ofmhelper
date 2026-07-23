"""
ofmhelpers/web/routers/todo.py

Admin-managed task list (model name + link to replicate + comments) that VAs
can see but not modify. Add/toggle/delete/export/import are admin-only --
enforced here, server-side, not just hidden in the template: the page itself
is reachable by both roles like every other page in the app, so a VA could
otherwise still POST straight to these endpoints.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, Response

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.auth import require_admin, ROLE_ADMIN
from ofmhelpers.web import todos

router = APIRouter(prefix="/todo", tags=["todo"])


@router.get("")
def form(request: Request):
    items = todos.list_todos()
    for t in items:
        t["created_at_display"] = datetime.fromtimestamp(t["created_at"]).strftime(
            "%Y-%m-%d %H:%M"
        )

    return templates.TemplateResponse(
        request,
        "todo_form.html",
        {
            "todos": items,
            "is_admin": request.session.get("role") == ROLE_ADMIN,
        },
    )


@router.post("/add")
def add(
    request: Request,
    model_name: str = Form(...),
    url: str = Form(...),
    comments: str = Form(""),
):
    require_admin(request)
    if not model_name.strip() or not url.strip():
        raise HTTPException(status_code=400, detail="Model name and URL are required")

    todos.add_todo(
        model_name.strip(), url.strip(), comments.strip(), request.session.get("role")
    )
    return RedirectResponse(url="/todo", status_code=303)


@router.get("/export")
def export(request: Request):
    require_admin(request)
    body = json.dumps(todos.list_todos(), indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="todos.json"'},
    )


@router.post("/import")
async def import_(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    raw = await file.read()
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Uploaded file is not valid JSON")
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="JSON must be a list of tasks")

    try:
        todos.import_todos(entries, request.session.get("role"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/toggle")
def toggle(request: Request, todo_id: str):
    require_admin(request)
    if not todos.toggle_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/delete")
def delete(request: Request, todo_id: str):
    require_admin(request)
    if not todos.delete_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return RedirectResponse(url="/todo", status_code=303)
