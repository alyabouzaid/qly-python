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
    def routing(self) -> Optional["Routing"]:
        """The receipt for a job submitted with ``device="auto"``, else None.

        Absent on every job that was not auto-routed, including every job from
        before the receipt existed.
        """
        return Routing.from_json(self.raw.get("routing"))

    @property
    def run_facts(self) -> "RunFacts":
        """What the device reported about this run.

        The compiled circuit, which physical qubits it landed on and how long it
        spent there. Empty until the job finishes, and sparse on devices that
        publish little — check a field for None rather than assuming it is set.
        """
        return RunFacts.from_json(self.raw.get("run_facts"))

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


@dataclass
class RunFacts:
    """What the device reported about the run it actually performed.

    Every field is a number a provider measured or a fact it stated. A fact the
    provider did not report is ``None`` (or an empty dict/list), never a
    plausible-looking default — ``None`` means "the device did not say", which
    is not the same as zero.
    """

    shots: Optional[int] = None
    #: Time on the device itself, in milliseconds, excluding queueing.
    execution_ms: Optional[float] = None
    predicted_ms: Optional[float] = None
    #: QPU seconds the device actually reported. None when the provider
    #: withheld the figure and Qly billed from an estimate instead.
    qpu_seconds: Optional[float] = None
    #: What the account was billed, measured or not. Equal to qpu_seconds when
    #: the device reported it; set alone when it did not.
    billed_qpu_seconds: Optional[float] = None
    #: Queue plus execution, on the provider's own clock.
    queue_and_run_ms: Optional[float] = None
    measured_qubits: List[int] = field(default_factory=list)
    #: Which physical qubits the vendor's compiler chose.
    physical_qubits: List[int] = field(default_factory=list)
    #: The circuit the device actually ran, as native OpenQASM.
    native_qasm: Optional[str] = None
    #: Gate histogram of that native circuit.
    native_gate_counts: Dict[str, int] = field(default_factory=dict)
    qubits_requested: Optional[int] = None
    rewiring: Optional[str] = None
    #: Gate histogram the device reported itself (IonQ).
    gate_counts: Dict[str, int] = field(default_factory=dict)
    device_warning: Optional[str] = None
    #: IonQ's error-mitigated distribution, as state -> probability, when it
    #: sent one. Verbatim from the device: compare it against ``Job.counts``
    #: rather than treating it as already applied to them.
    debiased_probabilities: Dict[str, float] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def two_qubit_gates(self) -> int:
        """How many two-qubit native gates the compiler emitted.

        The usual proxy for how much a NISQ run will be degraded by noise, since
        entangling gates dominate the error budget. Counts the native names the
        current providers emit; an unrecognised entangler is not counted, so
        treat this as a floor.
        """
        two_q = {"cz", "cx", "cnot", "ecr", "zz", "rzz", "xx", "ms", "gpi2_2q", "iswap", "swap"}
        return sum(n for g, n in self.native_gate_counts.items() if g.lower() in two_q)

    @classmethod
    def from_json(cls, d: Optional[Dict[str, Any]]) -> "RunFacts":
        d = d or {}
        return cls(
            shots=d.get("shots"),
            execution_ms=d.get("execution_ms"),
            predicted_ms=d.get("predicted_ms"),
            qpu_seconds=d.get("qpu_seconds"),
            billed_qpu_seconds=d.get("billed_qpu_seconds"),
            queue_and_run_ms=d.get("queue_and_run_ms"),
            measured_qubits=list(d.get("measured_qubits") or []),
            physical_qubits=list(d.get("physical_qubits") or []),
            native_qasm=d.get("native_qasm"),
            native_gate_counts=dict(d.get("native_gate_counts") or {}),
            qubits_requested=d.get("qubits_requested"),
            rewiring=d.get("rewiring"),
            gate_counts=dict(d.get("gate_counts") or {}),
            device_warning=d.get("device_warning"),
            debiased_probabilities=dict(d.get("debiased_probabilities") or {}),
            raw=d,
        )


@dataclass
class Estimate:
    """What a run will cost, before it runs.

    ``exact`` is the field to branch on. Per-shot and per-task billing is known
    before the job runs; per-QPU-second billing is not, because nobody knows how
    long the QPU will hold the circuit until it has. Those devices return
    ``exact=False`` with a ``cost_range_cents`` and a ``note`` instead of
    dressing an estimate up as a price.
    """

    provider: str
    device: str
    device_name: Optional[str] = None
    shots: Optional[int] = None
    billing: Optional[str] = None
    exact: bool = False
    cost_cents: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_formatted: Optional[str] = None
    cost_range_cents: Optional[Dict[str, int]] = None
    note: Optional[str] = None
    balance_cents: Optional[int] = None
    sufficient_balance: Optional[bool] = None
    warnings: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Estimate":
        return cls(
            provider=d.get("provider", ""),
            device=d.get("device", ""),
            device_name=d.get("device_name"),
            shots=d.get("shots"),
            billing=d.get("billing"),
            exact=bool(d.get("exact")),
            cost_cents=d.get("cost_cents"),
            cost_usd=d.get("cost_usd"),
            cost_formatted=d.get("cost_formatted"),
            cost_range_cents=d.get("cost_range_cents"),
            note=d.get("note"),
            balance_cents=d.get("balance_cents"),
            sufficient_balance=d.get("sufficient_balance"),
            warnings=list(d.get("warnings") or []),
            raw=d,
        )


@dataclass
class CalibrationMetric:
    """One published number about a device's current health."""

    label: str
    value: Any
    unit: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "CalibrationMetric":
        return cls(
            label=d.get("label", ""),
            value=d.get("value"),
            unit=d.get("unit"),
            raw=d,
        )


@dataclass
class Calibration:
    """Live calibration for one device, where the provider publishes it.

    ``source`` says where the numbers came from — ``"simulator"`` means there is
    no hardware calibration to report, and an empty ``metrics`` list means the
    provider does not publish any, not that the device is perfect.
    """

    device: str
    device_name: Optional[str] = None
    provider: Optional[str] = None
    type: Optional[str] = None
    qubits: Optional[int] = None
    source: Optional[str] = None
    fetched_at: Optional[str] = None
    connectivity: Optional[str] = None
    technology: Optional[str] = None
    note: Optional[str] = None
    metrics: List[CalibrationMetric] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_live(self) -> bool:
        """True when the provider published real calibration, not just a spec."""
        return bool(self.metrics) and self.source not in (None, "simulator", "spec")

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Calibration":
        return cls(
            device=d.get("device", ""),
            device_name=d.get("device_name"),
            provider=d.get("provider"),
            type=d.get("type"),
            qubits=d.get("qubits"),
            source=d.get("source"),
            fetched_at=d.get("fetchedAt") or d.get("fetched_at"),
            connectivity=d.get("connectivity"),
            technology=d.get("technology"),
            note=d.get("note"),
            metrics=[
                CalibrationMetric.from_json(m)
                for m in (d.get("metrics") or [])
                if isinstance(m, dict)
            ],
            raw=d,
        )




# -- device="auto" ---------------------------------------------------------------
#
# These mirror the receipt the server returns (docs/router/device-auto.md and the
# Routing / Candidate types in src/lib/router-auto.ts), field for field.
#
# Three rules, all of them Router's, and none of them are optional here:
#
#   * Nothing is computed, sorted, defaulted or reworded. Every sentence in a
#     receipt is written by the server (``because.text``, ``excluded.text``) and
#     is shown as sent.
#   * ``None`` means the server did not send the field, which is not the same as
#     zero or "no". Inside ``requested``, ``None`` means the caller did not
#     specify it. Unknown is always absent, never null.
#   * There is no score, rank or best field, and this class adds none. The
#     candidate list is ordered by the quantity you asked for, for this request
#     only. It is not a standing table.


def _opt_int_list(v: Any) -> Optional[List[int]]:
    return None if v is None else [int(x) for x in v]


@dataclass
class Exclusion:
    """Why a route cannot take this job. Branch on ``code``; show ``text`` as is."""

    code: str
    text: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Optional[Dict[str, Any]]) -> Optional["Exclusion"]:
        if not isinstance(d, dict):
            return None
        return cls(code=str(d.get("code", "")), text=str(d.get("text", "")), raw=d)


@dataclass
class Candidate:
    """One route to one machine, as compared for this request.

    ``eligible`` is True exactly when ``excluded`` is None, and a value is present
    exactly when eligible; the server enforces both. ``vendor_billed`` is a fact
    about what the vendor has billed Qly for jobs like this. It is never a price
    and is never what ``value`` was computed from.
    """

    machine: str
    machine_name: Optional[str] = None
    route: Optional[str] = None
    route_name: Optional[str] = None
    device: Optional[str] = None
    eligible: bool = False
    value: Optional[float] = None
    unit: Optional[str] = None
    #: "exact" or "estimate". For an estimate, ``range_cents`` is [low, high].
    basis: Optional[str] = None
    range_cents: Optional[List[int]] = None
    #: "qly" (your Qly credit) or "vendor_account" (billed elsewhere, to your own account).
    billed_by: Optional[str] = None
    vendor_billed: Optional[Dict[str, Any]] = None
    excluded: Optional[Exclusion] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Candidate":
        return cls(
            machine=str(d.get("machine", "")),
            machine_name=d.get("machine_name"),
            route=d.get("route"),
            route_name=d.get("route_name"),
            device=d.get("device"),
            eligible=bool(d.get("eligible")),
            value=d.get("value"),
            unit=d.get("unit"),
            basis=d.get("basis"),
            range_cents=_opt_int_list(d.get("range_cents")),
            billed_by=d.get("billed_by"),
            vendor_billed=d.get("vendor_billed"),
            excluded=Exclusion.from_json(d.get("excluded")),
            raw=d,
        )


@dataclass
class Chosen:
    """The route Qly chose. ``initial_layout`` None means default placement."""

    machine: str
    provider: str
    device: str
    device_name: Optional[str] = None
    initial_layout: Optional[List[int]] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Optional[Dict[str, Any]]) -> Optional["Chosen"]:
        if not isinstance(d, dict):
            return None
        return cls(
            machine=str(d.get("machine", "")),
            provider=str(d.get("provider", "")),
            device=str(d.get("device", "")),
            device_name=d.get("device_name"),
            initial_layout=_opt_int_list(d.get("initial_layout")),
            raw=d,
        )


@dataclass
class Because:
    """What the choice was made on. Print ``text``; it is the server's sentence."""

    metric: str
    value: Optional[float] = None
    unit: Optional[str] = None
    basis: Optional[str] = None
    source: Optional[str] = None
    #: True when candidates tied on the requested quantity and a tie-break decided.
    tie: Optional[bool] = None
    text: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Optional[Dict[str, Any]]) -> Optional["Because"]:
        if not isinstance(d, dict):
            return None
        return cls(
            metric=str(d.get("metric", "")),
            value=d.get("value"),
            unit=d.get("unit"),
            basis=d.get("basis"),
            source=d.get("source"),
            tie=d.get("tie"),
            text=str(d.get("text", "")),
            raw=d,
        )


@dataclass
class RoutingRequest:
    """What was asked. ``None`` in any field means the caller did not specify it."""

    device: Optional[str] = None
    prefer: Optional[str] = None
    providers: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    max_cost_cents: Optional[int] = None
    fallback: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Optional[Dict[str, Any]]) -> "RoutingRequest":
        d = d if isinstance(d, dict) else {}
        return cls(
            device=d.get("device"),
            prefer=d.get("prefer"),
            providers=d.get("providers"),
            exclude=d.get("exclude"),
            max_cost_cents=d.get("max_cost_cents"),
            fallback=d.get("fallback"),
            raw=d,
        )


@dataclass
class Routing:
    """The receipt for a ``device="auto"`` decision.

    ``chosen`` and ``because`` are None when nothing was eligible, which is the
    case for a refusal; ``candidates`` then says why for every route.
    """

    requested: RoutingRequest
    decided_at: Optional[str] = None
    chosen: Optional[Chosen] = None
    because: Optional[Because] = None
    candidates: List[Candidate] = field(default_factory=list)
    #: Each attempt as the server sent it, ``{"route": ..., "outcome": ...}``.
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, d: Optional[Dict[str, Any]]) -> Optional["Routing"]:
        if not isinstance(d, dict):
            return None
        return cls(
            requested=RoutingRequest.from_json(d.get("requested")),
            decided_at=d.get("decided_at"),
            chosen=Chosen.from_json(d.get("chosen")),
            because=Because.from_json(d.get("because")),
            candidates=[Candidate.from_json(c) for c in (d.get("candidates") or []) if isinstance(c, dict)],
            attempts=list(d.get("attempts") or []),
            raw=d,
        )


def devices_from_json(payload: Dict[str, Any]) -> List[Device]:
    return [Device.from_json(d) for d in payload.get("devices", [])]


def jobs_from_json(payload: Dict[str, Any]) -> List[Job]:
    return [Job.from_json(j) for j in payload.get("jobs", [])]
