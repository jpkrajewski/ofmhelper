"""Events out of the app, for anything that happens: a producer calls
`emit(Event(...))` or decorates a function with `@emits(build)`; the `events`
queue carries it to the aggregator (dispatch.py), which hands it to every sender
that is on (integrations/senders: Telegram, ...). A new event needs no change
here; a new sender is one package in integrations/senders."""

from ofmhelpers.features.events.bus import emit, emits
from ofmhelpers.features.events.dispatch import listening
from ofmhelpers.schemas.events import Event

__all__ = ["Event", "emit", "emits", "listening"]
