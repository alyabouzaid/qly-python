"""Typed data objects returned by the client.

These are thin wrappers over the JSON the API sends back. Unknown fields are
preserved on ``.raw`` so the library keeps working when the server adds things.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_DONE_STATES = {"COMPLETED", "DONE"}
_FAILED_STATES = {"FAILED", "ERROR", "CANCELLED"}


@dataclass
class Device:
    """A quantum device you can target in :meth:`Qly.submit`."""

    provider: str
    id: str
    name: str
    qubits: int
    type: str  # "real" or "simulator"
    status: str = "unknown"
    description: Optional[str] = None
    price: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_simulator(self) -> bool:
        return self.type == "simulator"

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Device":
        return cls(
            provider=d.get("provider", ""),
            id=d.get("id", ""),
            name=d.get("name", ""),
            qubits=int(d.get("qubits", 0) or 0),
            type=d.get("type", "unknown"),
            status=d.get("status", "unknown"),
            description=d.get("description"),
            price=d.get("price"),
            raw=d,
        )


@dataclass
class Balance:
    """Prepaid credit on the account."""

    cents: int
    usd: float
    formatted: str

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Balance":
        cents = int(d.get("balance_cents", 0) or 0)
        return cls(
            cents=cents,
            usd=float(d.get("balance_usd", cents / 100)),
            formatted=d.get("balance_formatted", f"${cents / 100:.2f}"),
        )


@dataclass
class Job:
    """A submitted job. Refresh it with :meth:`Qly.get_job`."""

    id: str
    provider: Optional[str] = None
    device: Optional[str] = None
    status: str = "UNKNOWN"
    shots: Optional[int] = None
    primitive: Optional[str] = None
    results: Optional[Dict[str, Any]] = None
    error: Any = None
    qasm: Optional[str] = None
    estimated_cost_usd: Optional[float] = None
    cost_cents: Optional[int] = None
    created_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def done(self) -> bool:
        """True once the job has reached a terminal state (success or failure)."""
        if self.raw.get("done") is True:
            return True
        return self.status.upper() in _DONE_STATES or self.status.upper() in _FAILED_STATES

    @property
    def succeeded(self) -> bool:
        return self.status.upper() in _DONE_STATES

    @property
    def failed(self) -> bool:
        return self.status.upper() in _FAILED_STATES

    @property
    def counts(self) -> Optional[Dict[str, int]]:
        """Measurement histogram, if the job produced one (Sampler jobs)."""
        if isinstance(self.results, dict):
            counts = self.results.get("counts")
            if isinstance(counts, dict):
                return counts
        return None

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Job":
        return cls(
            id=str(d.get("id", "")),
            provider=d.get("provider"),
            device=d.get("device"),
            status=str(d.get("status", "UNKNOWN")),
            shots=d.get("shots"),
            primitive=d.get("primitive"),
            results=d.get("results"),
            error=d.get("error"),
            qasm=d.get("qasm"),
            estimated_cost_usd=d.get("estimated_cost_usd"),
            cost_cents=d.get("cost_cents"),
            created_at=d.get("created_at"),
            raw=d,
        )


def devices_from_json(payload: Dict[str, Any]) -> List[Device]:
    return [Device.from_json(d) for d in payload.get("devices", [])]


def jobs_from_json(payload: Dict[str, Any]) -> List[Job]:
    return [Job.from_json(j) for j in payload.get("jobs", [])]
