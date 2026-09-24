"""qly — Python client for the Qly quantum computing platform.

    from qly import Qly
    client = Qly(api_key="qly_live_...")
"""

from .client import Qly
from .exceptions import (
    APIError,
    AuthenticationError,
    CircuitError,
    InsufficientBalanceError,
    JobFailedError,
    JobTimeoutError,
    QlyError,
    RateLimitError,
    RoutingRefusedError,
)
from .models import (
    Balance,
    Because,
    Calibration,
    CalibrationMetric,
    Candidate,
    Chosen,
    Device,
    Estimate,
    Exclusion,
    Job,
    Routing,
    RoutingRequest,
    RunFacts,
)
from .version import __version__

__all__ = [
    "Qly",
    "Job",
    "RunFacts",
    "Device",
    "Calibration",
    "CalibrationMetric",
    "Estimate",
    "Balance",
    "QlyError",
    "AuthenticationError",
    "InsufficientBalanceError",
    "CircuitError",
    "RoutingRefusedError",
    "Routing",
    "RoutingRequest",
    "Candidate",
    "Chosen",
    "Because",
    "Exclusion",
    "RateLimitError",
    "JobFailedError",
    "JobTimeoutError",
    "APIError",
    "__version__",
]
