---
name: code-rules
description: The code rules of this repo, the owner's own words. Load BEFORE writing, changing, reviewing or debugging any Python file (src, tests, scripts, alembic, hooks); the skills guard refuses a Python edit until it is loaded. Patterns to use, what is banned (uppercase constants, dynamic unpacking, model_copy(update=), list comprehensions, abbreviated names, runtime registration, swallowed errors), services as classes with composition, ABC only, explicit over dynamic, validation at the schema.
---

Patterns to use: Composite, Dependency Injection, Facade.

Uppercase constants are banned. _VALUE and VALUE must go into constants.py and be defined using frozen Pydantic models or Enums when they represent values of the same type, such as strings.

Dynamic unpacking is banned. dict(value) is bad. Use explicit attribute access instead: x = value.x, z = value.z. Similarly, **dict(value) is an anti-pattern. Use {"x": value.x} so we can see exactly what is being passed.

PydanticModel.model_copy(update=...) is banned. Just do PydanticModel.x = value. This also ensures that attempts to modify immutable objects raise errors instead of bypassing immutability through model_copy(update=...).

Pydantic models that don't need imports from ofmhelpers belong in schemas.py. Models that import application code and transform data belong in models.py.

Services are classes, not standalone functions. Services group related functions and receive clients and repositories through composition.

Web routes must inject services through Depends, not standalone functions. This also enforces how services are created.

The core of this application is Storage and AI Creation. They must define their own services and functions.

UI is just as important as the backend. Deliver working UI, click through it, and discover bugs or missing functionality.

List comprehensions are banned because they are unreadable. Use regular for loops and explicitly define the types of items in lists.

Use typing throughout the codebase.

Do not repeat the same mistake twice.

Exceptions belong in errors.py within the same module for consistency.

Avoid abbreviated variable names. For example, c is an anti-pattern:

if any(_says(e, c.login_words + c.throttled_words) for e in tries):

Use full, descriptive variable names.

Do not redefine constants locally. For example:

from ofmhelpers.features.clone_links.constants import clone_links_constants

THUMB_KEY = clone_links_constants().thumb_key
TOO_SLOW = clone_links_constants().too_slow
CANNOT_OPEN = clone_links_constants().cannot_open

This is just cheating. Use the constants directly where they are needed.

Make small changes that complete the task. Delete code when possible. Every code change should make the code cleaner.

Swallowing errors is forbidden. Errors must be logged. Unimportant logs must not be propagated to Logfire.

Write code that is understandable without comments or Markdown files. The codebase must be self-documenting through its code and tests.

Runtime registration, global mutable state, import-time side effects, decorator-based metaprogramming, implicit dependency discovery, and dynamic discovery are forbidden.

For example:

def register_provider[P: type[Provider]](cls: P) -> P:
    _registered().classes[cls.name] = cls
    return cls

This is forbidden. Explicit registration through a registry or dependency injection is mandatory.

Use ABC exclusively; never bypass, weaken, or circumvent contracts or strict typing to make code work—fix the underlying design instead.

Explicitd code instead of dyanmic

Maximum possible validation of data should be at schema fastapi level, for example list length, url parsing the shecma should take care of this,
and prepare a validated object for a service, so we dont depend on the fastapi contract

use Field(default_factory=...) for models with literal mutable defaults instead of (= [] and = {})

itertools are banned in this repo agents create unredable code instead of using simple loops

noqa:  adding noqa is banned in this repo this is a sign of bad design, not linter being picky, when you need to add noqa - stop, think whats wrong about your design and prepare better solution

use TDD, becouse that forces you to first understand the problem, not just write a implementation and write passing tests
