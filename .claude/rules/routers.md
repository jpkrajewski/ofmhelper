---
paths:
  - "src/ofmhelpers/web/routers/**"
  - "src/ofmhelpers/web/main.py"
  - "src/ofmhelpers/web/dependencies.py"
---

# Routers: MUST

Before the first edit here: load the `fastapi` skill. Depth: `rest-api` skill (Zalando rule text by id). Enforced by `tests/unit/architecture/` (baseline only shrinks).

## Shape of a route
- A route is: parameters -> parsed and checked by `Depends` / `Query` / `Path` / `Form` / `Body` -> ONE call into `features/` -> return the response or the Pydantic object. At most 25 lines.
- No `ofmhelpers.db` import, no `ofmhelpers.integrations` import, no loop over data, no parsing, no transforming, no business rule. That lives in `features/<page>/`.
- The route body makes no queue call (`enqueue`, `JobQueue`). The feature starts the work; a dependency may hand the queue to the feature (`_encodes` in `video_tools/trials.py`).
- Dependencies are written `Annotated[T, Depends(...)]` and given a name (`CurrentUser`, `Scope`). Never `= Depends(...)` as a default. Shared by several routers: `web/dependencies.py`; used by one router: in that router.
- A dependency loads AND checks (exists, belongs to this user) and raises there; dependencies build on each other.
- 3+ routes repeating the same lookup (same path parameter, same user and scope) = ONE dependency that builds the feature object once (`Trials` in `video_tools/trials.py`). Ruff `FAST003` cannot see a path parameter behind a dependency alias: add that router to its per-file ignore in `pyproject.toml`. One or two routes: the path parameter stays on the route and the lookup is one plain feature call that raises.
- Every route declares what it returns: a return type or `response_model=`. Not both with the same class.
- Request data is typed: a form of 3+ fields or one shared by routes is a model (`Annotated[XForm, Form()]`, `web/forms.py`); 1-2 fields stay `Annotated[str, Form()]`. Never `request.form()` / `request.json()` in a route body; a form whose fields come from data (`/ai/run`) is read in a dependency. A webhook reading raw bytes for a signature is fine.
- A `/run` route and any route doing blocking I/O is `def`, not `async def` (`test_run_routes_off_event_loop.py`).
- Work that must survive, retry or be seen goes on a queue through a feature call, never `BackgroundTasks`.
- No `except Exception` that swallows. Cleanup-and-re-raise (the pay-twice guard) belongs in the feature, not the route.
- A refusal worded for the page is an `HTTPException` or a feature error with a plain sentence. Never `detail=str(exc)` of an outside service's error.

## HTTP, for new and changed routes (Zalando MUST, FastAPI form)
Existing URLs are not renamed (bookmarks, provider callbacks) unless the owner asks.
Where Zalando, FastAPI and the house rules pull apart, the line says who wins.
- `[134]` plural resource names, `[129]` kebab-case path segments, `[136]` no trailing slash, no empty segment: a collection root under a prefix is `@router.get("")`, not `"/"`.
- `[141]` `[138]` no verbs in the path: the method is the verb, the path is the thing (`POST /trials/{id}/reels`, not `/createReel`). An action with no resource is a sub-resource noun (`/reels/{key}/selection`). House wins in two places: `POST /<prefix>/run` is the name for starting a tool's job, and a page form deletes with `POST .../delete` (HTML forms cannot send `DELETE`); a new fetch-only route uses `DELETE`.
- `[143]` `[228]` a thing is found by path segments with URL-safe ids; `[142]` names from the domain, not from the code.
- `[130]` query parameters snake_case; `[137]` the usual names: `q`, `sort`, `fields`, `limit`, `cursor`/`offset`.
- `[148]` `[149]` methods mean what HTTP says: `GET` reads and changes nothing, `PUT` and `DELETE` can be repeated safely, `POST` creates or starts. A `POST` that starts paid work is safe against a double click. Allowed: a `GET` that warms a cache or queues a repeat-safe prepare (`/edit`), and the `GET` a provider returns to (OAuth callbacks).
- `[243]` `[220]` `[151]` real status codes, the most specific one, set on the decorator for a new route (`status_code=201` create, `202` job started, `204` nothing to return); errors by `HTTPException` or a typed error, never a 200 with an error inside. An existing route keeps 200 until its callers and tests are checked for comparing against 200.
- `[176]` NOT applied (FastAPI and the house win): an error is `{"detail": "<plain sentence>"}`, which the page scripts read; a non-string `detail` shows as "[object Object]".
- `[177]` no stack trace or raw exception text in a response.
- `[167]` `[118]` `[110]` JSON out of a new route is an object with snake_case names (`items: list[T]`), never a bare list or string. FastAPI allows `list[T]` and the three existing list routes keep it (`[106]`); no `RootModel`.
- `[123]` `[122]` absent and `null` mean the same; a boolean is never `null`.
- `[169]` `[238]` dates and times are ISO 8601 with timezone (`datetime`, not a formatted string); ids, URLs, emails use their Pydantic types.
- `[159]` a list that can grow is paged.
- `[106]` `[115]` never break a caller: add fields, do not rename or remove; no `/v2/` in a path.
- `[104]` every route is behind sign-in and returns only the caller's own data, unless it is on the public list on purpose.
- `[101]` the OpenAPI that FastAPI generates is the spec: `summary`, `tags`, `status_code` and response models are filled in so it reads true.
