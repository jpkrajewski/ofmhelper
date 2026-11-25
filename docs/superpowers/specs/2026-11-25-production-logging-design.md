# Production logging design

## Intent

Make web, sweep, CLI, and RQ worker logs structured, queryable, safe, and useful during failures without coupling every module to application configuration.

## Decisions

- Application modules and migrations use `logging.getLogger(__name__)` directly. The `ofmhelpers.log.get_logger` wrapper is removed.
- `ofmhelpers.log` is a stdlib-only bootstrap that loads `logging.json` with `logging.config.dictConfig`. It does not import application settings or Pydantic.
- Root handlers emit JSON Lines to stdout and Logfire. Docker owns container log files and rotates them with the `json-file` driver, 25 MB per file and three generations. The application does not manage log files.
- JSON records contain UTC ISO 8601 timestamps, level, logger, message, process/thread, exception data, trace/span IDs, acting context, and structured fields supplied as `extra={"context": {...}}`.
- A monitoring-side filter adds OpenTelemetry and `acting` context. Another filter redacts messages, nested context, RQ arguments, and known sensitive mapping keys while preserving safe debugging values.
- RQ job descriptions use dotted function names rather than serialized job arguments. Job logs inherit task and job IDs from the existing `acting` context.
- Logfire remains batched and optional. Its handler factory returns a null handler when telemetry is disabled. Sentry keeps its existing error integration and redaction.
- No `QueueHandler`, application file handler, structlog dependency, or new service is introduced.

## Acceptance criteria

- Stdout is one parseable JSON object per record in development and production.
- Structured context and trace/job correlation survive formatting; unknown objects are serialized safely.
- Secrets in messages, URLs, exceptions, nested context, and RQ arguments are masked while job IDs and other safe arguments remain visible.
- Logging configuration has no application-settings dependency and repeated configuration does not duplicate records.
- Every web/worker application service has bounded Docker log rotation.
- Scoped unit and integration tests pass in Docker, including logging, telemetry, Sentry, RQ, and compose checks.
