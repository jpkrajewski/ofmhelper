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
`web`, `background` -> `features` -> `integrations`, `media` -> `db`, `cache`, `monitoring`, `config`. Imports go down only, and no two packages import each other (`tests/unit/architecture/test_no_mutual_imports.py`).
- **Integration client** (`integrations/<name>/`): talks to ONE outside service. Takes and returns Pydantic models. Knows nothing of features, pages, users' flows or the database. Turns the service's errors into its own typed errors. Has an interface, a fake and a contract test.
- **Service** (`features/<page>/service.py`): the logic. Uses clients and repositories. No HTTP objects (`Request`, `Response`, `HTTPException`), no templates.
- **Feature** (`features/<page>/`): what one page or job does: `service.py`, `jobs.py`, `models.py`; math and static data in their own files.
- **Repository / store** (`db/`): the only code that touches the database. Returns models, not rows or dicts. Reads and writes rows, nothing else: what calls an integration (a provider client, a password hash, a file check) is the feature's.
- **Schema** (`<module>/schema.py`): a module's Pydantic shapes, in the lowest module that makes them. A shape a store keeps is `db/<table>/schema.py`, and the integration or page above imports it from there (`db/tracked_accounts/schema.py`, `db/watched_profiles/schema.py`); one only an integration makes is `integrations/<name>/schema.py`; one only a page makes is `features/<page>/schema.py`. Static data checked against one lives beside it (`db/devices/catalog/`, `db/short_links/catalog/`). A page's own view model that needs an integration or `media` is `features/<page>/models.py`.
- Never upward (`features` importing `web`, `integrations` importing `features`), never around (a router calling a client). Need something from above: move it down, or pass it in as an argument.

## See the import graph
`scripts/import_graph.py` reads every import under `src/ofmhelpers` (plain and `lazy`). From the repo root in Git Bash:
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps tests uv run --frozen python scripts/import_graph.py` prints the summary (areas and who imports whom, layer breaks, packages importing each other, cycles, most imported) and writes `reports/deps/index.html` (gitignored): open it in a browser for the area diagram, the package table and a box to look up one module.
- Same command + a module name (`... scripts/import_graph.py features.trials.folder`) prints only what that module imports and what imports it.
- Checked on every test run: `tests/unit/architecture/test_architecture.py` `test_layers_import_downwards_only` (an area imports only areas of a lower rank, `RANK`) and `tests/unit/architecture/test_no_mutual_imports.py` (no two packages import each other, top level and second level). Not checked yet: longer cycles (A -> B -> C -> A) and cycles between modules inside a package; the script lists them under CYCLES.

## Functions
- What a function returns is checked, not written here: ruff makes every function in `src/` declare its return type, and `tests/unit/architecture/test_architecture.py` `test_every_function_returns_a_model_or_a_plain_value` fails on a dict, a tuple, a Mapping, a TypedDict, a NamedTuple or a bare `Any`/`object` (Pydantic validator hooks and `__iter__` excepted; an iterator / generator may yield tuples, never a dict).
- 3+ functions with the same leading parameters, differing in the last one or two = ONE class holding the shared ones, the functions its methods (`features/trials/folder.py` `Trials`). An RQ job stays a module-level function under its name: a thin one that builds the object and calls the method (`features/trials/tasks.py` `Folder`).
- The shared-parameters rule is checked in every package but `web/`, the return rule in all of `src/` (`tests/unit/architecture/`). How a return is fixed: a table looked up by a name only known at run time (provider classes, catalog rows) is a read-only `types.MappingProxyType[str, T]`; a fixed set of named values is a `TypedDict`. Never a class wrapping a dict and serving it back (`__getitem__`, `get`, `names`, `all`, ...): `test_no_class_is_a_dict_in_disguise` fails on one (owner 2026-11-20: "its basicly just a dict"). Two values together are a small frozen model (`media/size.py` `Size`). A provider payload is a model dumped at the call (`features/clone/targets.py` `CloneValues`); fields whose names are catalog data are `integrations/ai_providers/spec.py` `Fields` (`model_dump()` = the plain fields). A type alias is no way round: the check follows a name to what it stands for (`Payload = dict[...]`, `type X = ...`, a class on `NamedTuple`), also through an import. A web module may import a shape (`db/<table>/schema.py`), never a store, repository, session or integration.
- One function, one job. A function that fetches, decides and stores is three functions.
- Dependencies come in as arguments or through a cached accessor, never a module-level instance.

## Failures
- Nothing fails silently: no bare `except`, no swallowed error, no default returned in place of an error. Log with `exc_info=True`; the error shows on the span of its request or job.
- An outside call that can fail has a timeout, a typed error and, where it costs money, protection against running twice.
