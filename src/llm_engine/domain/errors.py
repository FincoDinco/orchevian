from __future__ import annotations


class EngineError(Exception):
    """Engine failure with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message if message is not None else code)
