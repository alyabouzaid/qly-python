"""Exceptions raised by the Qly client."""

from __future__ import annotations

from typing import Any, Optional


class QlyError(Exception):
    """Base class for every error this library raises."""


class AuthenticationError(QlyError):
    """The API key is missing, malformed, or has been revoked."""


class InsufficientBalanceError(QlyError):
    """The account does not have enough prepaid credit to run the job.

    The ``estimated_cents`` and ``balance_cents`` attributes carry what the
    server reported, when available.
    """

    def __init__(
        self,
        message: str,
        estimated_cents: Optional[int] = None,
        balance_cents: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.estimated_cents = estimated_cents
        self.balance_cents = balance_cents


class RateLimitError(QlyError):
    """Too many requests in the current window. ``retry_after`` is in seconds."""

    def __init__(self, message: str, retry_after: Optional[int] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class JobFailedError(QlyError):
    """A job finished in a FAILED / ERROR / CANCELLED state.

    ``job`` holds the final :class:`~qly.models.Job` so the caller can inspect
    the provider's error detail.
    """

    def __init__(self, message: str, job: Any = None) -> None:
        super().__init__(message)
        self.job = job


class JobTimeoutError(QlyError):
    """``run()`` gave up waiting for the job to finish."""


class APIError(QlyError):
    """The server returned an error that does not map to a more specific class.

    ``status_code`` is the HTTP status and ``payload`` is the decoded body, if
    the server sent JSON.
    """

    def __init__(
        self, message: str, status_code: Optional[int] = None, payload: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
