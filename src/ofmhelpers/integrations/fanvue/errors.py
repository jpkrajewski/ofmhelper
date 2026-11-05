"""What the Fanvue client raises."""

from ofmhelpers.integrations.chat_source import ChatSourceError


class FanvueError(ChatSourceError):
    """Fanvue refused or failed; the message is safe to show."""


class FanvueRefusedError(FanvueError):
    """Fanvue says this cannot be read at all: asking again changes nothing."""


class FanvueAuthError(FanvueError):
    """The connection is gone: someone has to connect Fanvue again."""
