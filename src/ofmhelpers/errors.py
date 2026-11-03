"""The root of the app's own errors. Each family (`ProviderError`, `DriveError`,
`LLMError`, ...) keeps its module and subclasses; this is what lets one handler
say "ours, its message is safe to show" apart from a bug."""


class AppError(Exception):
    """An expected failure with a message worded for the person using the app."""
