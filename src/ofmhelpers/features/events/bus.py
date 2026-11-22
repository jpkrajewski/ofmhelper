import asyncio
import functools
import inspect
from typing import Any, ParamSpec, TypeVar, cast
lazy from collections.abc import Awaitable, Callable

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.config import settings
from ofmhelpers.features.events.dispatch import dispatch
from ofmhelpers.integrations.senders.schema import Event
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def _absolute(event: Event) -> Event:
    base = settings.web.base_url.rstrip("/")
    if event.url and event.url.startswith("/") and base:
        return event.model_copy(update={"url": f"{base}{event.url}"})
    return event


def emit(event: Event) -> None:
    try:
        if not isinstance(event, Event):
            msg = f"emit takes an Event, not {type(event).__name__}"
            raise TypeError(msg)  # noqa: TRY301
        JobQueue(name=QueueName.EVENTS).put(
            dispatch, (_absolute(event).model_dump(mode="json"),)
        )
    except Exception:
        logger.warning(
            "event %s not queued",
            event.name if isinstance(event, Event) else type(event).__name__,
            exc_info=True,
        )


def _emit_built(
    fn: Callable[..., Any],
    build: Callable[..., Any],
    result: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    try:
        event = build(result, *args, **kwargs)
    except Exception:
        logger.warning("event of %s not built", fn.__qualname__, exc_info=True)
        return
    if event is not None:
        emit(event)


def _refuse(fn: Any) -> None:
    if isinstance(fn, classmethod | staticmethod):
        msg = "@emits goes under @classmethod/@staticmethod, not above"
        raise TypeError(msg)
    if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
        msg = f"@emits cannot wrap a generator ({fn.__qualname__})"
        raise TypeError(msg)


def emits(
    build: Callable[..., Event | None],
) -> Callable[[Callable[P, R]], Callable[P, R]]:

    def wrap(fn: Callable[P, R]) -> Callable[P, R]:
        _refuse(fn)
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def run_async(*args: P.args, **kwargs: P.kwargs) -> R:
                result = await cast("Awaitable[R]", fn(*args, **kwargs))
                await asyncio.to_thread(_emit_built, fn, build, result, args, kwargs)
                return result

            return cast("Callable[P, R]", run_async)

        @functools.wraps(fn)
        def run(*args: P.args, **kwargs: P.kwargs) -> R:
            result = fn(*args, **kwargs)
            _emit_built(fn, build, result, args, kwargs)
            return result

        return run

    return wrap
