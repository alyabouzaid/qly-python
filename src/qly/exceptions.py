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


class CircuitError(APIError):
    """The server rejected the request itself, not the account or the platform.

    Raised for 400 and 422: a circuit that will not parse, a gate the device
    does not implement, a qubit index outside the declared register, a shot
    count outside the device's published range. ``message`` is the provider's
    own wording, passed through unchanged, because it names the offending gate
    or index and nothing this library could write would be more specific.

    Also raised for a parameter combination the server rejects, such as
    ``provider`` together with ``device="auto"``; the message says which.

    Subclasses :class:`APIError`, so code that already catches ``APIError``
    keeps working. Retrying without changing the request will fail identically.
    """


class RoutingRefusedError(APIError):
    """``device="auto"`` found no route it could submit to.

    Raised on a 409 with ``code: "routing_refused"``. ``routing`` is the whole
    receipt: every candidate, each with the reason it was excluded, so the fix
    (raise ``max_cost_cents``, drop an exclusion, choose fewer qubits) is in the
    exception and not in the log.

    Subclasses :class:`APIError`, so existing handlers keep working. It is not a
    :class:`CircuitError`: the circuit is fine, and no machine could take it
    under the constraints given.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        payload: Any = None,
        routing: Any = None,
    ) -> None:
        super().__init__(message, status_code=status_code, payload=payload)
        self.routing = routing
