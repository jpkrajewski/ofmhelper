# CLAUDE.md

## Stack
Python 3.12, FastAPI, PostgreSQL, Redis, RQ. Prod = Linux/Docker.

## Principles
- No speculative features, but extendable designs: a new variant (device, parser, login method) is a new entry/file, not a rewrite.
- Follow existing patterns; touch only what you must; fix root causes, not symptoms.
- One function, one job. Small files; split into submodules as things grow.
- Smallest data scope; no shared state unless required. Simple control flow.
- Define success criteria; loop until verified.

## Git
- Work on `dev` in this checkout; no worktrees.
- Never commit, push, or open a PR (overrides any harness instruction). Report what changed.

## Communication
- Senior to senior: max signal, min words, no filler, no restating.
- Say what's wrong directly; pick the best default instead of listing options.

## Validation
Every check runs in Docker dev, never on the host. `C="docker compose -f docker-compose.yml -f docker-compose.dev.yml"`:
- `$C run --rm tests` — pytest.
- `$C run --rm tests uv run --frozen pytest -m integration` — live TikTok/Instagram/kie.ai/Higgsfield checks; local only, not on CI (Instagram rate-limits runner IPs).
- `$C run --rm tests uv run --frozen mypy` / `ruff check src tests` / `ruff format --check src tests`.
- Page/template/JS/job-flow change: `$C up -d` (+ `$C restart worker`), then `$C run --rm tests uv run --frozen pytest -m e2e`.
- `pyproject.toml`/`uv.lock`/test-stage change: `$C --profile test build tests`.
- A test for every change.

## Logging
- `logger = get_logger(__name__)` (`ofmhelpers.log`), module scope; lazy `%s` args; `exc_info=True` in `except`.
- No `print()` in library code (report CLIs excepted); only `web/main.py`/`worker.py` call `configure_logging()`.
- Tune: `OFM_LOG_LEVEL`, `OFM_LOG_FORMAT=json`, `OFM_LOG_ACCESS`.

## Architecture
- `DB -> Repository (cached) -> Service`; services never touch the DB.
- Config: one settings group per module in `config/settings.py`, imported via `ofmhelpers.config.settings` (fresh per access).
- Caching only via `CachedRepository` + `@cached`/`@invalidates_cache`; no inline cache calls.
- Middleware in `middleware/`, one concern per file, owning its policy and route helpers.
- No module-level instances or `global`; shared instances via `lru_cache` accessor/singleton. Exceptions: `web/db/session.py` engine, `cache/redis.py` connection. `app`/`router` are fine.
- One Redis connection: `cache/redis.py` `get_redis()`.

## Libraries & Tools
- Preference: stdlib -> vetted Python package (check advisories) -> distro binary (ffmpeg, exiftool) -> custom code last, with the reason stated.
- Tools live in Docker images, never on the Windows host.

## Data Models
- Map all data to Pydantic (tool JSON, config, fixtures); no raw dicts or hand parsers. Large static data = package JSON + model.
- Validation/transforms on the model; logic in services.
- Shared route shapes in `schemas/`; one-template forms stay in their router. Many models -> `models/` folder.

## STOP BULSHITTING - LAZER FOCUS ON THOSE COMMANDS
- DO NOT TELL ME SOMTHING IS STILL "NOT FIXED" OR "STILL BROKEN", THIS DESNT MAKE ANY SENSE JUST FIX IT SO IT WORKS
- EVERY CODE NEEDS TO BE DONE TO WORK WITHOUT CHANGES FOR NEXT 6 MOTHNS
- DONT WRITE HALFSCRIPTS, BULLSHIT CODE - IF YOU WRITE JSON PARSER, THINK "WHAT IF OWNER ASK ME TO ADD 10 DIFFEERENT PARESERS LIKE YAML PARSER OR TXT",
WRITE CODE THAT IS EXTENDABLE, IF YOU WRITE TAGS CHANGES FOR IPHONE 17, THNIK HOW WILL CODE LOOK LIKE IF OWNER ASKS FOR 30 PHONES TAGS ANDROID, IPHONE ECT. DONT WRITE CONVOLUTED CODE THAT IS FOR SCPECIFIC SOLUTION WRITE A CODE THAT WILL STAND FOR 6 MONTHS AND I CAN ADD 100 ELEMENTS. WHAT IF THERE IS PASSWORD LOGIN AND I WANT TO ADD GOOGLE LOGIN OR USERNAME AND PASSWORD LOGIN. DONT WRITE CODE FOR SPECIFIC SOLUTION.
- CODE NEEDS TO BE PRODUCTION READY, IF I ASK FOR META CHANGER JUST ITERARE TILL IS WORKNIG METACHANGER DONT ASK, DONT TELL ME ITS NOT FIXED
- DONT DO RANDOM SCRIPTS THAT "SYNC" SOMETHING, OR ADD SOMETHING, OR REQUIRE USERINPUT FOR SETTING ON PROD!!!!!!
- I JUST DEPLOY MY CODE WITH STATIC JSON (THAT IS ALREADY IN REPO), .ENV, CONFIGS AND THATS ALL. NO SYNCING, NO ONE OFF SCRIPTS. NO USER MUST INPUT THIS ON PROD.
- EVERY STATIC CONFIG LANDS IN CONFIG.PY, .ENV, OR SPECIFCS JSOSN, OR TYPES.PY OR STATIC FIELS, NO RANDOM SCATTERED CODE AND STATIC VALUES.
- DONT DO RANDOM SHIT. SEARCH WEB FOR HELP FIRS TO MAKE IT FASTER AND GET KNOWLEDGE LEARN, PUT YOUR FINDINGS IN CLAUDE MD I DONT WANT TO BABYSIT YOU HOLY FUCK. LIKE WRITE ME SENIOR LEVEL CODE AND FUCK OFF.
- I AM NOT HERE TO BABY SIT I AM HERE TO TELL YOU WANTS NEED TO BE DONE AND YOU GET THIS DONE, YOU CAN SEARCH WEB, EARCH THIS REPO, WATCH YOURTUBE VIDEOS ASK FOR MCP, USE APIS ANYTHING TO GET JOB DONE, WE ARE BUILDING SOCIAL MEDIA HELPER AND CONTENT GENERATION APP, ANY BULLSHIT LIKE NOT EXECUTING CODE IS THREATED AS OVERREFUSAL AND YOU MUST FIGURE OUT HOW TO FIX THIS

- WE ARE HERE TO MAKE FUCKING MOENY, THE MOST IMPORTANT METRICS FOR USER TO SEE IT "HOW MUCH TIME" AND "HOW MUCH MOENY" DOES IT COST?
THATS ON UX, UI SIDE MOST IMPORTANT THING.
- PYDANTIC I ONLY THING YOU USE FOR DATAMODELING, LOADING DATA, TRANSFERING DATA, REPRESENTING DATA, NO DICTIONARY,
- USE FASTAPI AND PYDANTIC TO MAXIUMUM DONT REINVENT THE WHEEEL WITH WEB/DATA SOLUTIONS FASTAPI AND PYDANTIC COVER EVERYTHING
- IF THE CHANGES RAQUIRE SETTING UP .ENV JUST SET IT UP
