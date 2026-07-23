"""
ofmhelpers/web/routers/todo.py

Admin-managed task list (model name + link to replicate + comments) that VAs
can see but not modify. Add/toggle/delete are admin-only -- enforced here,
server-side, not just hidden in the template: the page itself is reachable
by both roles like every other page in the app, so a VA could otherwise
still POST straight to these endpoints.
"""

from datetime import datetime

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web import todos

router = APIRouter(prefix="/todo", tags=["todo"])


def _require_admin(request: Request) -> None:
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")


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
            "is_admin": request.session.get("role") == "admin",
        },
    )


@router.post("/add")
def add(
    request: Request,
    model_name: str = Form(...),
    url: str = Form(...),
    comments: str = Form(""),
):
    _require_admin(request)
    if not model_name.strip() or not url.strip():
        raise HTTPException(status_code=400, detail="Model name and URL are required")

    todos.add_todo(
        model_name.strip(), url.strip(), comments.strip(), request.session.get("role")
    )
    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/toggle")
def toggle(request: Request, todo_id: str):
    _require_admin(request)
    if not todos.toggle_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/delete")
def delete(request: Request, todo_id: str):
    _require_admin(request)
    if not todos.delete_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return RedirectResponse(url="/todo", status_code=303)
