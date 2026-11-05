"""What the Stacked client raises."""

from ofmhelpers.integrations.chat_source import ChatSourceError


class StackedError(ChatSourceError):
    """Stacked refused or failed; the message is safe to show."""


class StackedAuthError(StackedError):
    """The API key is wrong or revoked."""


class StackedForbiddenError(StackedError):
    """The key cannot reach that creator, or lacks the scope."""
