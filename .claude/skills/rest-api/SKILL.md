---
name: rest-api
description: HTTP and REST rules for this app's FastAPI routes: Zalando's RESTful API Guidelines (the 85 MUST rules, full text by id) and the FastAPI best-practice list for agents, mapped to FastAPI. Use when creating or refactoring a router, naming a URL or query parameter, choosing an HTTP method or status code, shaping a JSON response or error, adding pagination, or changing a route other code or a provider calls.
---

# REST rules for FastAPI routes

The rules that apply to every route load by themselves from `.claude/rules/routers.md` when a router file is touched (about 25 Zalando MUSTs plus the clean-router rules). This skill is the full text behind an id, and the rest of the MUST list.

## Files
- `must.md`: all 85 MUST rules, `id | chapter | title`. Grep it by keyword.
- `rules/<id>.md`: Zalando's text for that rule (reasons, examples, exceptions). Read the one you need.
- Refresh: `MSYS_NO_PATHCONV=1 $C run --rm --no-deps -v "$(pwd -W)/.claude/skills/rest-api:/skill" tests python /skill/build.py` (`C` = the dev compose pair).

Source and licence: Zalando RESTful API and Event Guidelines, https://opensource.zalando.com/restful-api-guidelines/ (`#<id>` jumps to a rule), repo `zalando/restful-api-guidelines`, CC BY 4.0. Text in `rules/` is theirs, cut per rule (code samples reformatted by the repo's ruff).

## How a Zalando rule becomes FastAPI
| Rule area | In FastAPI |
|---|---|
| URL shape `[134] [129] [136] [141] [143]` | `APIRouter(prefix="/things")`, `@router.get("/{thing_id}")`; no trailing slash, kebab-case, nouns |
| Query names `[130] [137]` | `Annotated[int, Query(ge=1, le=100)]`; a group of them as one Pydantic model: `Annotated[Filters, Query()]` |
| Methods `[148] [149]` | one HTTP operation per function; `GET` never writes |
| Status codes `[243] [220] [151]` | `status_code=` on the decorator; other outcomes in `responses={...}`; `HTTPException` or a typed error with a handler |
| Errors `[176] [177]` | one exception handler returns the error body (`application/problem+json` for JSON routes); never the raw exception text |
| JSON shape `[118] [110] [123] [122]` | return a Pydantic model (an object, snake_case fields); `response_model_exclude_none` when absent and `null` mean the same |
| Formats `[169] [238] [171]` | `datetime` / `date` / `UUID` / `AnyUrl` / `EmailStr` / `Decimal` fields, not strings |
| Pagination `[159]` | a `Page[T]` model (`items` + `next` cursor), `limit` and `cursor` as `Query` |
| Compatibility `[106] [115]` | add optional fields only; never rename; no version in the path |
| Security `[104] [105]` | a dependency on the router: `APIRouter(dependencies=[Depends(...)])` or the `CurrentUser` / `Scope` dependencies |
| Spec `[101] [192]` | FastAPI's generated OpenAPI: fill `summary`, `tags`, `response_model`, `responses` |

## What does not apply here, and why
- This app is mostly server-rendered pages: HTML routes return a `Response` from `render(...)`. URL, method, status and security rules apply to them; JSON payload rules apply only to routes returning data.
- Hostnames, audiences, API ids `[215] [219] [224]`, hypermedia `[162]`-`[166]`, events `[194]`-`[246]`, Zalando headers `[233] [184]`: written for a company with many public APIs. Skip unless the owner asks for a public API.
- Existing URLs are not renamed to fit (bookmarks, provider callbacks). The rules are for new and changed routes.

## FastAPI best practices for agents (zhanymkanov)
Full file: https://github.com/zhanymkanov/fastapi-best-practices/blob/master/AGENTS.md (no licence stated: link, do not copy). What applies here is already in `.claude/rules/routers.md`: `Annotated` dependencies, validate inside dependencies, chain and reuse them, typed request models, no `BackgroundTasks` for work that needs retry, no broad `except` around a route, document routes, no deprecated Pydantic v1 API.
Not taken, on purpose: async SQLAlchemy and `async def` by default (this app's routes are sync `def`, see `test_run_routes_off_event_loop.py`), JWT, its `src/<domain>/router.py` layout (ours: `CLAUDE.md` "Layout").
Version questions: the `fastapi` skill (the pinned version's own guide) wins over any list on the web.
