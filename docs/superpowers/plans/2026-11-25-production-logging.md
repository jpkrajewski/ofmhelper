# Production Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace application-coupled text logging with stdlib-only JSON configuration, structured correlated records, selective redaction, and bounded Docker logs.

**Architecture:** `log.py` loads static JSON and contains no application imports. Monitoring-side formatter/filters own record shaping and redaction; existing `acting` and OpenTelemetry contexts provide correlation. All application modules use stdlib loggers, while Docker owns stdout file retention.

**Tech Stack:** Python 3.15 stdlib logging, FastAPI, RQ, OpenTelemetry/Logfire, Sentry, Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-production-logging-design.md`

## Global Constraints

- No comments or docstrings in code; no `getattr`, `vars()`, or `__dict__`.
- No new packages or services; no commit, push, or deploy.
- Preserve the unrelated `.gitignore` modification.
- Run scoped tests through `scripts/test.sh`; do not run the full suite before the owner's ship request.

## Review Focus

- Sensitive values inside nested job arguments must be masked without hiding safe values.
- Records emitted outside a request/job must still format with empty correlation context.
- Non-JSON-native context values must not break the logging call.
- Repeated configuration and RQ forks must not duplicate or lose records.
- Missing Logfire credentials must not enable export or produce a second text stream.

### Task 1: JSON bootstrap and structured records

**Files:**
- Create: `src/ofmhelpers/logging.json`
- Create: `src/ofmhelpers/monitoring/log_records.py`
- Modify: `src/ofmhelpers/log.py`
- Modify: `src/ofmhelpers/monitoring/telemetry.py`
- Test: `tests/unit/test_log.py`, `tests/integration/web/test_telemetry.py`

**Interfaces:**
- Produces: `configure_logging(*, force: bool = False) -> None`
- Produces: `JsonFormatter`, `RedactFilter`, `ContextFilter`
- Consumes: `extra={"context": {...}}` on stdlib logging calls

- [ ] Replace the text-format tests with failing tests for JSONL, UTC timestamps, nested context, safe serialization, exception redaction, repeated configuration, and disabled Logfire export.
- [ ] Run `scripts/test.sh tests/unit/test_log.py tests/integration/web/test_telemetry.py` and confirm the new assertions fail.
- [ ] Implement static JSON loading, the formatter/filters, and a null Logfire handler when disabled. Use explicit `record.context` access with an `AttributeError` default rather than introspection.
- [ ] Re-run the scoped tests and confirm they pass.

### Task 2: Remove the wrapper and application settings

**Files:**
- Modify: every `src/ofmhelpers/**/*.py`, `alembic/**/*.py`, and affected test importing `get_logger`
- Modify: `src/ofmhelpers/config/settings.py`, `src/ofmhelpers/config/__init__.py`
- Modify: `docker-compose.yml`, `docker-compose.dev.yml`, `tests/e2e/conftest.py`

**Interfaces:**
- Produces: module-level `logger = logging.getLogger(__name__)`
- Removes: `get_logger`, `LoggingSettings`, `OFM_LOG_LEVEL`, `OFM_LOG_FORMAT`, `OFM_LOG_ACCESS`

- [ ] Add a failing architecture assertion that application code neither imports `ofmhelpers.log.get_logger` nor references `LoggingSettings`.
- [ ] Dry-run the mechanical replacement on a copied source tree inside the tests container; import the copied application.
- [ ] Apply the replacement to the checkout, remove the obsolete settings group and environment entries, and delete the wrapper.
- [ ] Run `scripts/test.sh tests/unit/test_log.py tests/unit/test_sweeps_entrypoint.py tests/unit/test_worker_entrypoint.py` and confirm imports and entrypoints pass.

### Task 3: Useful worker context and selective redaction

**Files:**
- Modify: `src/ofmhelpers/cache/queue.py`
- Modify: `src/ofmhelpers/monitoring/log_records.py`
- Modify: `src/ofmhelpers/redact.py` only if assignment-form redaction is needed by the tests
- Test: `tests/unit/test_event_telegram_text.py`, `tests/integration/events/test_event_bus.py`, `tests/integration/web/test_telemetry.py`

**Interfaces:**
- Consumes: existing `Acting(via, actor_id, viewing_id, ref, trace_id)`
- Produces: trace/span IDs plus actor/via/ref and parsed job/task IDs on log context
- Produces: RQ descriptions containing only `module.qualname`

- [ ] Replace blanket-hiding assertions with failing assertions that safe RQ arguments survive, sensitive mapping values are masked, job descriptions contain no arguments, and job logs carry job/task/trace context.
- [ ] Run the named scoped tests and confirm they fail.
- [ ] Implement dotted RQ descriptions and the smallest redaction/context changes needed.
- [ ] Re-run the named scoped tests and confirm they pass.

### Task 4: Docker retention, documentation, and verification

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.claude/rules/runtime.md`, `.claude/skills/aws-devops-guidance/foundational.md`, `src/ofmhelpers/web/CLAUDE.md` if needed
- Modify: `backlog/IMPROVEMENTS.md` to remove only fixed findings
- Test: `tests/unit/test_worker_services.py`

**Interfaces:**
- Produces: Docker `json-file` rotation with `max-size: 25m` and `max-file: "3"` on every application web/worker service

- [ ] Add a failing compose test asserting bounded Docker logging for web, sweeps, and every worker.
- [ ] Add a shared Compose logging anchor and apply it to those services.
- [ ] Update project rules and findings to describe stdlib loggers and JSON/Docker behavior; remove the two completed logging backlog findings.
- [ ] Run `scripts/test.sh tests/unit/test_log.py tests/unit/test_event_telegram_text.py tests/unit/test_worker_services.py tests/unit/test_sweeps_entrypoint.py tests/unit/test_worker_entrypoint.py tests/integration/web/test_telemetry.py tests/integration/events/test_event_bus.py tests/integration/test_sentry.py`.
- [ ] Run Docker mypy, host Ruff with the pinned version, `check_no_comments.py`, and `check_no_getattr.py` on changed files; inspect `git diff` and `git status`.
- [ ] Exercise real web and worker logging in dev, parse emitted JSONL, and verify a safe job argument, a masked secret, and trace/job context.
