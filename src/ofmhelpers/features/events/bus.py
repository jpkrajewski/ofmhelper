"""Where an event enters: `emit(Event(...))` anywhere, or `@emits(build)` on any
function. Both return at once: the event joins the `events` queue, and the
aggregator (dispatch.py) takes it from there on the events worker.

A producer never fails because of an event: a queue that cannot take it, a
`build` that raises or returns something else than an `Event`, anything given to
`emit` that is not an `Event` -- each is logged and the producer carries on. Only
making an `Event` with a wrong field raises (pydantic, where it is made): a bug in
the producer, which its test finds.

    @emits(lambda link, *_: Event(
        name="links.clicks",
        title=f"{link.clicks} clicks on {link.name}",
        url="/links",
        key=f"links.clicks:3000:{link.id}",
    ) if link.clicks >= 3000 else None)
    def refresh(link_id: int) -> Link: ...

An event is sent when `emit` runs, not when a surrounding database transaction
commits: emit after the write it reports.
"""

import asyncio
import functools
import inspect
from typing import Any, ParamSpec, TypeVar, cast
lazy from collections.abc import Callable

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.config import settings
from ofmhelpers.features.events.dispatch import dispatch
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.events import Event

logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def _absolute(event: Event) -> Event:
    """A page of this site (`/links`) as a link a phone can open."""
    base = settings.web.base_url.rstrip("/")
    if event.url and event.url.startswith("/") and base:
        return event.model_copy(update={"url": f"{base}{event.url}"})
    return event


def emit(event: Event) -> None:
    """`event` to every sender that is on; never raises."""
    try:
        if not isinstance(event, Event):
            msg = f"emit takes an Event, not {type(event).__name__}"
            raise TypeError(msg)  # noqa: TRY301 -- logged below like any failure
        JobQueue(QueueName.EVENTS).put(
            dispatch, _absolute(event).model_dump(mode="json")
        )
    except Exception:
        logger.warning(
            "event %s not queued", getattr(event, "name", "?"), exc_info=True
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
    """What @emits cannot wrap, refused when decorating instead of failing later."""
    if isinstance(fn, classmethod | staticmethod):
        msg = "@emits goes under @classmethod/@staticmethod, not above"
        raise TypeError(msg)
    if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
        msg = f"@emits cannot wrap a generator ({fn.__qualname__})"
        raise TypeError(msg)


def emits(
    build: Callable[..., Event | None],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """After the function returns, `build(result, *args, **kwargs)`: an `Event` is
    emitted, None is nothing. The function's result and exceptions are its own
    (one that raises emits nothing). On an async function the queueing runs off
    the event loop."""

    def wrap(fn: Callable[P, R]) -> Callable[P, R]:
        _refuse(fn)
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def run_async(*args: P.args, **kwargs: P.kwargs) -> Any:
                result = await fn(*args, **kwargs)
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
