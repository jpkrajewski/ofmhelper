class AnalysisError(ValueError):
    """The provider's response wasn't a usable Seedance prompt. Carries the
    provider's raw text so the caller can show it instead of nothing."""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw
