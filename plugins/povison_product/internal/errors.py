"""Error types for Povison product lookup."""

from __future__ import annotations


class PovisonProductError(Exception):
    """Base error for Povison product lookup failures."""


class PovisonConfigError(PovisonProductError):
    """Raised when the tool is missing required configuration."""


class PovisonParseError(PovisonProductError, ValueError):
    """Raised when a Povison product link cannot be parsed."""


class PovisonAPIError(PovisonProductError):
    """Raised when the Povison API returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, api_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code
