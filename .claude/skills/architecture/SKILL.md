---
name: architecture
description: The bigger picture before code: where a new thing belongs in this app's layers and which pattern it follows (repository, service layer, adapter for an outside service, unit of work, events, commands), from "Architecture Patterns with Python" (cosmicpython) mapped to this repo. Use before adding a feature, a new integration, a new module or class, before moving logic between router, feature, integration and db, and when a change makes one layer reach into another.
---

# Architecture: decide where it goes before writing it

The MUST rules load by themselves from `.claude/rules/layers.md`. This skill is the reasoning and the book behind them.

Book: "Architecture Patterns with Python", Percival and Gregory, free at https://www.cosmicpython.com/book/preface.html. Licence CC BY-NC-ND: link and summarize in own words, never copy its text into the repo. Read ONE chapter when a decision needs it (WebFetch the URL), not the book.

## The pattern, where it lives here, when to read the chapter
| Pattern | In this repo | Read when | Chapter |
|---|---|---|---|
| Domain model: rules in plain objects, no I/O | Pydantic models with their validation in `features/<page>/models.py`, `schemas/` | logic is growing inside a service or a router | `/book/chapter_01_domain_model.html` |
| Repository: storage behind an interface | `db/repositories/` (the only DB code), `db/stores/` | adding a table, or a service wants a session | `/book/chapter_02_repository.html` |
| Coupling and abstractions: test with a fake, not a mock | interface + fake + contract test for every integration | adding an outside service | `/book/chapter_03_abstractions.html` |
| Service layer: one function per use case, HTTP-free | `features/<page>/service.py`; a route makes ONE call into it | a route is doing more than parse, call, return | `/book/chapter_04_service_layer.html` |
| High gear / low gear tests | integration test first for behaviour, unit tests for logic | deciding what kind of test | `/book/chapter_05_high_gear_low_gear.html` |
| Unit of work: one transaction per use case | each job's write is its own transaction; repositories own commits | a change must be all-or-nothing across tables | `/book/chapter_06_uow.html` |
| Aggregates: one owner of a consistency rule | a job row; a `Trials` folder (`features/trials/folder.py`) | two writers can break a rule (double click, race) | `/book/chapter_07_aggregate.html` |
| Events and message bus | `features/events/` (bus, dispatch) -> `integrations/senders/` | one thing happening should trigger others | `/book/chapter_08_events_and_message_bus.html` |
| Commands vs events | `start_job(...)` = a command (do this); an emitted event = a fact (this happened) | naming a new job or event | `/book/chapter_10_commands.html` |
| Adapters for outside systems | `integrations/<name>/` | a provider's shape is leaking into a feature | `/book/chapter_11_external_events.html` |
| CQRS: reads may skip the model | page queries read straight through a repository into a view model | a page needs a join the write model does not have | `/book/chapter_12_cqrs.html` |
| Dependency injection | FastAPI `Depends` at the edge; `lru_cache` accessors inside | passing a client or repository around | `/book/chapter_13_dependency_injection.html` |

Chapter URLs are `https://www.cosmicpython.com` + the path.

## The check, before the first line of a new thing
1. **Layer**: router, feature/service, integration client, repository, or schema? One answer. Two answers = two things.
2. **Neighbour**: which existing file does the same job for another page or provider? Open it and follow its shape (names, file split, test style).
3. **Direction**: does every import go down (`web` -> `features` -> `integrations`/`media` -> `db`/`cache`/`schemas`/`config`/`utils`)? If something below needs something above, move that thing down or pass it in.
4. **Edges**: what crosses each boundary is a Pydantic model. What can fail at each outside call, and what does the caller see?
5. **Record**: if it runs in the background, what does its job row say when done and when failed (`rq` skill)?
6. Say the answers in two or three lines before coding. If they do not fit the existing layout, say that instead of inventing a new folder.

`tests/unit/architecture/test_architecture.py` checks 1 and 3 mechanically; its `layers` list in `baseline.json` is empty since 2026-11-11: any upward import fails the test.
