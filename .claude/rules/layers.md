---
paths:
  - "src/ofmhelpers/features/**"
  - "src/ofmhelpers/integrations/**"
  - "src/ofmhelpers/media/**"
---

# Layers: MUST

Depth: `architecture` skill (the patterns behind this, one chapter at a time). Enforced by `tests/unit/architecture/` (baseline only shrinks; the function rules' groups are empty).

Before a new file or class: say in one line which layer it is and which existing file it follows. No existing file to follow = stop and look again; the pattern is almost always here already.

## Who may know whom
`web` -> `features` -> `integrations`, `media` -> `db`, `cache`, `schemas`, `config`, `utils`. Imports go down only.
- **Integration client** (`integrations/<name>/`): talks to ONE outside service. Takes and returns Pydantic models. Knows nothing of features, pages, users' flows or the database. Turns the service's errors into its own typed errors. Has an interface, a fake and a contract test.
- **Service** (`features/<page>/service.py`): the logic. Uses clients and repositories. No HTTP objects (`Request`, `Response`, `HTTPException`), no templates.
- **Feature** (`features/<page>/`): what one page or job does: `service.py`, `jobs.py`, `models.py`; math and static data in their own files.
- **Repository / store** (`db/`): the only code that touches the database. Returns models, not rows or dicts. Reads and writes rows, nothing else: what calls an integration (a provider client, a password hash, a file check) is the feature's.
- **Schemas** (`schemas/`): plain shapes every layer shares, and the static data they are checked against (package JSON + model: `phone_catalog/`, `link_catalog/`). A shape an integration returns AND a store keeps or a page shows lives here (`socials.py`, `instagram.py`, `gdrive.py`, `downloads.py`, `cookies.py`); the integration re-exports it. A page's own view model that needs an integration or `media` is `features/<page>/models.py`.
- Never upward (`features` importing `web`, `integrations` importing `features`), never around (a router calling a client). Need something from above: move it down, or pass it in as an argument.

## Functions
- A function returns a Pydantic object or `None`; many = a list of them. Never a dict, a tuple or loose values. Only a single plain value (bool, int, str, Path) stays plain.
- 3+ functions with the same leading parameters, differing in the last one or two = ONE class holding the shared ones, the functions its methods (`features/trials/folder.py` `Trials`). An RQ job stays a module-level function under its name: a thin one that builds the object and calls the method (`features/trials/tasks.py` `Folder`).
- Checked in every package but `web/` (`tests/unit/architecture/`). A table looked up by name (provider classes, catalog rows) is `utils/registry.py` `Registry[T]`, not a dict. Two values together are a small frozen model (`media/size.py` `Size`). A provider payload is a model dumped at the call (`features/clone/targets.py` `CloneValues`); fields whose names are catalog data are `integrations/ai_providers/spec.py` `Fields` (`model_dump()` = the plain fields). A type alias is no way round: the check follows a name to what it stands for (`Payload = dict[...]`, `type X = ...`, a class on `NamedTuple`/`TypedDict`), also through an import.
- One function, one job. A function that fetches, decides and stores is three functions.
- Dependencies come in as arguments or through a cached accessor, never a module-level instance.

## Failures
- Nothing fails silently: no bare `except`, no swallowed error, no default returned in place of an error. Log with `exc_info=True`; the error shows on the span of its request or job.
- An outside call that can fail has a timeout, a typed error and, where it costs money, protection against running twice.
