---
name: pydantic
description: "Pydantic v2 in this app: the rules agents broke, plus Pydantic's own concept docs for the release in VERSION, one page at a time. Use before writing or changing a Pydantic model or settings class (BaseModel, BaseSettings, RootModel, TypeAdapter), a Field, a ConfigDict, a validator or serializer, or code that loads, copies, dumps or reads models (model_validate, model_validate_json, model_dump, model_copy, from_attributes)."
---

# Pydantic in this app

The guard `skills` (`.claude/hooks/guards/skills.py`) asks for this skill before an edit writes a Pydantic class or calls the model API. Load it, read the page for what you are about to write, then write it the Pydantic way.

## What agents got wrong (owner 2026-11-22: "you dont know how to fucking write and use Pydantic")
- **Pydantic already loads JSON.** `Spec.model_validate_json(path.read_text("utf-8"))` is the whole loader. Never validate and then copy fields over one by one (`for f in loaded.model_fields_set: setattr(...)`). `docs/models.md` "Validating data", `docs/json.md`.
- **Pydantic already reads objects.** An ORM row or any object with attributes: `Model.model_validate(row, from_attributes=True)` (or `model_config = ConfigDict(from_attributes=True)`). `docs/models.md` "Arbitrary class instances". The literal example (`db/clone_from_links/repository.py`, 2026-11-22):
  ```python
  # wrong: a hand-copied column list and getattr
  return CloneFromLink.model_validate({name: getattr(row, name) for name in _COLUMNS})
  # right: Pydantic reads the row itself
  return CloneFromLink.model_validate(row, from_attributes=True)
  ```
- **`getattr`, `vars()` and `__dict__` are banned in the whole repo** (owner 2026-11-22). An argparse `Namespace` or any object into a model is `Model.model_validate(obj, from_attributes=True)`, never `Model.model_validate(vars(obj))`. `scripts/check_no_getattr.py` runs in pre-commit, `tests/unit/architecture/test_no_getattr.py` on the whole repo, and the `no_getattr` guard refuses an edit that adds one. A model has typed fields: read `model.field`, dump with `model.model_dump()`, iterate with `for name, value in model` (`docs/serialization.md` "Iterating over models"). A name picked at run time is a dict or a `MappingProxyType` keyed by that name, not an attribute lookup.
- **`frozen=True` only for a value that never changes** after it is made (a stage, a draw, a report). A builder or a spec you fill step by step is a plain model you change in place (`spec.stages.append(...)`, `spec.device = name`). Never freeze it and then `model_copy(update=...)` on every call. `docs/models.md` "Model copy", `docs/fields.md` "Immutability".
- **Config a person or agent writes (a JSON spec) is `extra="forbid"`**, so a typo fails instead of falling back to a default. `docs/models.md` "Extra data", `docs/config.md`.
- **`model_copy(update=...)` does not validate.** A dict in `update` stays a dict, even where the field is a model. Build the new value as a model, or validate it (`Model.model_validate({...})`). `docs/models.md` "Model copy".
- **Mutable defaults**: `Field(default_factory=list)`; Pydantic copies a plain `[]` default per instance, but say what you mean. `docs/fields.md` "Mutable default values".
- **Settings** come from `config/settings.py` groups only (`.claude/rules/code.md`), on `pydantic-settings` (`docs/pydantic_settings.md`).

## Pydantic's docs (`docs/`, version in `VERSION`)
Grep `docs/` for the term, then Read that one section. Do not read a whole page unless you need it.

| File | What is in it |
|---|---|
| `docs/models.md` | BaseModel basics, model methods, data conversion, extra data, nested models, validating data (`model_validate`, `model_validate_json`, `model_construct`), error handling, arbitrary class instances (`from_attributes`), model copy, generic models, `RootModel` |
| `docs/fields.md` | `Field`, the annotated pattern, defaults and `default_factory`, aliases, constraints, strict fields, discriminators, immutability (`frozen`), excluding fields, computed fields |
| `docs/config.md` | `ConfigDict` on models, dataclasses, `TypeAdapter`, `validate_call`; config propagation |
| `docs/validators.md` | `field_validator`, `model_validator`, before/after/wrap/plain, raising errors, validation info and context, ordering |
| `docs/serialization.md` | `model_dump`, `model_dump_json`, python vs json mode, include/exclude, iterating over models, field and model serializers, subclasses |
| `docs/json.md` | JSON parsing (partial, cached strings), JSON serialization |
| `docs/types.md` | standard and utility types, `MISSING` sentinel, custom types with `Annotated` and `__get_pydantic_core_schema__` |
| `docs/unions.md` | union modes, discriminated unions |
| `docs/alias.md` | `AliasPath`, `AliasChoices`, alias generators, alias precedence |
| `docs/type_adapter.md` | `TypeAdapter`: validating a type that is not a model (`list[Model]`, a union) |
| `docs/dataclasses.md` | Pydantic dataclasses and how they differ from models |
| `docs/strict_mode.md` | strict validation, per field or per model |
| `docs/pydantic_settings.md` | `BaseSettings`, env vars, dotenv, sources |
| `docs/json_schema.md` | JSON Schema generation and customisation |
| `docs/forward_annotations.md` | forward references, `model_rebuild` |
| `docs/validation_decorator.md` | `validate_call` |
| `docs/conversion_table.md` | what each input type converts to, strict and lax |
| `docs/performance.md` | `TypeAdapter` reuse, discriminated unions, `model_validate_json` over `json.loads` + `model_validate` |
| `docs/experimental.md` | experimental APIs: do not use without the owner's yes |

Refresh (a new Pydantic release): `MSYS_NO_PATHCONV=1 $C run --rm --no-deps -v "$(pwd -W)/.claude/skills/pydantic:/skill" tests python /skill/build.py v<version>` (`C` = the dev compose pair; tag = the latest release, `https://api.github.com/repos/pydantic/pydantic/releases/latest`; uv.lock pins 2.14.1 on 2026-11-22). Source: https://github.com/pydantic/pydantic/tree/main/docs/concepts (MIT licence), site https://docs.pydantic.dev/latest/.
