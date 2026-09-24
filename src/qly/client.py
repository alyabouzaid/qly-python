"""The Qly client.

Example
-------
    from qly import Qly

    client = Qly(api_key="qly_live_...")     # or set QLY_API_KEY
    job = client.run(
        qasm=bell_qasm,
        provider="ibm",
        device="ibm_kingston",
        shots=1024,
    )
    print(job.counts)
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, NoReturn, Optional, Union

import requests

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
    Calibration,
    Device,
    Estimate,
    Job,
    Routing,
    devices_from_json,
    jobs_from_json,
)
from .version import __version__

DEFAULT_BASE_URL = "https://qly.app"
_USER_AGENT = f"qly-python/{__version__}"


class Qly:
    """Client for the Qly quantum platform API.

    Parameters
    ----------
    api_key:
        Your ``qly_live_...`` key. Falls back to the ``QLY_API_KEY`` environment
        variable. Create one at https://qly.app/settings/api-keys.
    base_url:
        Override the API host (mostly for self-hosted or staging). Falls back to
        ``QLY_BASE_URL``, then https://qly.app.
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        key = api_key or os.environ.get("QLY_API_KEY")
        if not key:
            raise AuthenticationError(
                "No API key. Pass api_key=... or set the QLY_API_KEY environment "
                "variable. Create a key at https://qly.app/settings/api-keys."
            )
        self.api_key = key
        self.base_url = (base_url or os.environ.get("QLY_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            }
        )

    # -- HTTP plumbing ------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.request(
                method, url, json=json, params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise QlyError(f"Network error talking to {url}: {exc}") from exc

        payload: Any
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": resp.text}

        if resp.ok:
            return payload if isinstance(payload, dict) else {"data": payload}

        self._raise_for_status(resp.status_code, payload)

    @staticmethod
    def _raise_for_status(status: int, payload: Any) -> NoReturn:
        message = ""
        if isinstance(payload, dict):
            message = payload.get("error") or payload.get("message") or ""
        message = message or f"HTTP {status}"

        if status == 401:
            raise AuthenticationError(message)
        if status == 402:
            est = payload.get("estimatedCents") if isinstance(payload, dict) else None
            bal = payload.get("balanceCents") if isinstance(payload, dict) else None
            # The server says what the run costs and what is left; the next step
            # is the part a script cannot work out for itself.
            raise InsufficientBalanceError(
                f"{message} Add credit at https://qly.app/pricing, or target a free "
                f"simulator — see GET /api/v1/devices for which devices cost nothing.",
                estimated_cents=est,
                balance_cents=bal,
            )
        if status == 429:
            retry = None
            if isinstance(payload, dict):
                retry = payload.get("retryAfterSeconds")
            raise RateLimitError(message, retry_after=retry)
        if status == 409 and isinstance(payload, dict) and payload.get("code") == "routing_refused":
            # Nothing was eligible. The receipt says why, route by route.
            raise RoutingRefusedError(
                message,
                status_code=status,
                payload=payload,
                routing=Routing.from_json(payload.get("routing")),
            )
        if status in (400, 422):
            # The request is the thing that has to change. Keep the provider's
            # wording — it names the gate or index that was rejected.
            raise CircuitError(message, status_code=status, payload=payload)
        raise APIError(message, status_code=status, payload=payload)

    # -- Devices & balance --------------------------------------------------

    def devices(self) -> List[Device]:
        """List the quantum devices available to your account."""
        return devices_from_json(self._request("GET", "/api/v1/devices"))

    def balance(self) -> Balance:
        """Return your current prepaid credit balance."""
        return Balance.from_json(self._request("GET", "/api/v1/balance"))

    def calibration(self, device: Union[str, Device]) -> Calibration:
        """Live calibration for one device, where the provider publishes it.

        :meth:`devices` answers "what can I target"; this answers "is it any
        good right now". Pass a device id or a :class:`Device` from
        :meth:`devices`.
        """
        device_id = device.id if isinstance(device, Device) else device
        return Calibration.from_json(
            self._request("GET", "/api/v1/calibration", params={"device": device_id})
        )

    def estimate(
        self,
        qasm: Optional[str] = None,
        *,
        provider: str,
        device: str,
        shots: int = 1024,
        primitive: str = "sampler",
        observables: Optional[List[str]] = None,
        circuit: Optional[Any] = None,
    ) -> Estimate:
        """What a run would cost, without submitting anything.

        Prices with the same helpers the submit path bills with, so an estimate
        and the charge that follows cannot drift apart. Check ``.exact`` before
        treating ``.cost_cents`` as a price: QPU-second devices can only give a
        range until the run has happened.
        """
        if circuit is not None:
            if qasm is not None:
                raise ValueError("Pass either qasm= or circuit=, not both.")
            qasm = _circuit_to_qasm(circuit)

        body: Dict[str, Any] = {
            "provider": provider,
            "device": device,
            "shots": shots,
            "primitive": primitive,
        }
        if qasm is not None:
            body["qasm"] = qasm
        if observables:
            body["observables"] = observables

        return Estimate.from_json(self._request("POST", "/api/v1/estimate", json=body))

    # -- Jobs ---------------------------------------------------------------

    def submit(
        self,
        qasm: Optional[str] = None,
        *,
        provider: Optional[str] = None,
        device: str,
        shots: int = 1024,
        primitive: str = "sampler",
        observables: Optional[List[str]] = None,
        circuit: Optional[Any] = None,
        qiskit: Optional[str] = None,
        ionq_native: Optional[Dict[str, Any]] = None,
        device_name: Optional[str] = None,
        prefer: Optional[str] = None,
        providers: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        max_cost_cents: Optional[int] = None,
        fallback: Optional[str] = None,
    ) -> Job:
        """Submit a circuit and return immediately with a queued :class:`Job`.

        With ``device="auto"`` Qly picks the machine: pass ``prefer`` and leave
        ``provider`` out. See :meth:`route` to see the decision without
        submitting. ``prefer``, ``providers``, ``exclude``, ``max_cost_cents``
        and ``fallback`` are sent as given and only when given, with no default
        here, so a missing ``prefer`` reaches the server and you see its message.

        Provide the circuit in exactly one of these ways:

        * ``qasm`` — an OpenQASM 2.0 string.
        * ``circuit`` — a Qiskit ``QuantumCircuit`` (needs the ``qiskit`` extra).
        * ``ionq_native`` — a native IonQ program dict.

        ``primitive="estimator"`` requires ``observables`` (Pauli strings such as
        ``["ZZ", "IZ"]``) and is currently IBM-only.
        """
        if circuit is not None:
            if qasm is not None:
                raise ValueError("Pass either qasm= or circuit=, not both.")
            qasm = _circuit_to_qasm(circuit)

        if qasm is None and ionq_native is None:
            raise ValueError("Provide a circuit via qasm=, circuit=, or ionq_native=.")

        if primitive == "estimator" and not observables:
            raise ValueError("primitive='estimator' requires observables, e.g. ['ZZ'].")

        body: Dict[str, Any] = {
            "device": device,
            "shots": shots,
            "primitive": primitive,
            "observables": observables or [],
        }
        if provider is not None:
            body["provider"] = provider
        if qasm is not None:
            body["qasm"] = qasm
        if qiskit is not None:
            body["qiskit"] = qiskit
        if ionq_native is not None:
            body["ionq_native"] = ionq_native
        if device_name is not None:
            body["device_name"] = device_name
        body.update(_routing_fields(prefer, providers, exclude, max_cost_cents, fallback))

        return Job.from_json(self._request("POST", "/api/v1/jobs", json=body))

    def route(
        self,
        qasm: Optional[str] = None,
        *,
        circuit: Optional[Any] = None,
        shots: int = 1024,
        prefer: Optional[str] = None,
        providers: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        max_cost_cents: Optional[int] = None,
        fallback: Optional[str] = None,
    ) -> Routing:
        """Decide where ``device="auto"`` would run a circuit, and say why.

        Submits nothing and charges nothing. Returns the receipt: the route
        chosen, the sentence saying what it was chosen on
        (``routing.because.text``), and every candidate with either its number or
        the reason it was excluded.

        Raises :class:`RoutingRefusedError` when no route is eligible; the
        exception carries the same receipt as ``.routing``.

        Nothing here scores or ranks machines and no default is applied for
        ``prefer``: leave it out and the server says so.
        """
        if circuit is not None:
            if qasm is not None:
                raise ValueError("Pass either qasm= or circuit=, not both.")
            qasm = _circuit_to_qasm(circuit)

        body: Dict[str, Any] = {"device": "auto", "shots": shots}
        if qasm is not None:
            body["qasm"] = qasm
        body.update(_routing_fields(prefer, providers, exclude, max_cost_cents, fallback))

        payload = self._request("POST", "/api/v1/route", json=body)
        routing = Routing.from_json(payload.get("routing"))
        if routing is None:
            raise QlyError("The server answered /api/v1/route without a routing receipt.")
        return routing

    def get_job(self, job: Union[str, Job]) -> Job:
        """Fetch the latest status and results for a job."""
        job_id = job.id if isinstance(job, Job) else job
        return Job.from_json(self._request("GET", f"/api/v1/jobs/{job_id}"))

    def jobs(self, limit: int = 20) -> List[Job]:
        """List your most recent jobs (newest first)."""
        return jobs_from_json(
            self._request("GET", "/api/v1/jobs", params={"limit": limit})
        )

    def wait(
        self,
        job: Union[str, Job],
        *,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
        raise_on_failure: bool = True,
    ) -> Job:
        """Block until a job reaches a terminal state.

        Raises :class:`JobTimeoutError` if ``timeout`` seconds pass first, and
        :class:`JobFailedError` if the job failed (unless ``raise_on_failure``
        is False).
        """
        deadline = time.monotonic() + timeout
        current = self.get_job(job)
        while not current.done:
            if time.monotonic() >= deadline:
                raise JobTimeoutError(
                    f"Job {current.id} still {current.status} after {timeout:.0f}s."
                )
            time.sleep(poll_interval)
            current = self.get_job(current)

        if current.failed and raise_on_failure:
            raise JobFailedError(f"Job {current.id} ended in {current.status}.", job=current)
        return current

    def run(
        self,
        qasm: Optional[str] = None,
        *,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
        **submit_kwargs: Any,
    ) -> Job:
        """Submit a circuit and wait for it to finish. Returns the final job.

        Accepts every keyword :meth:`submit` does, plus ``poll_interval`` and
        ``timeout`` for the wait loop.
        """
        job = self.submit(qasm, **submit_kwargs)
        return self.wait(job, poll_interval=poll_interval, timeout=timeout)


def _routing_fields(
    prefer: Optional[str],
    providers: Optional[List[str]],
    exclude: Optional[List[str]],
    max_cost_cents: Optional[int],
    fallback: Optional[str],
) -> Dict[str, Any]:
    """The ``device="auto"`` fields, only those the caller gave, unchanged.

    No defaults and no checks. A missing ``prefer`` is the server's to refuse, so
    that there is one statement of the rule and it is the server's own.
    """
    fields: Dict[str, Any] = {}
    if prefer is not None:
        fields["prefer"] = prefer
    if providers is not None:
        fields["providers"] = providers
    if exclude is not None:
        fields["exclude"] = exclude
    if max_cost_cents is not None:
        fields["max_cost_cents"] = max_cost_cents
    if fallback is not None:
        fields["fallback"] = fallback
    return fields


def _circuit_to_qasm(circuit: Any) -> str:
    """Serialize a Qiskit QuantumCircuit to OpenQASM 2.0."""
    # Qiskit >= 1.0 dropped QuantumCircuit.qasm(); qasm2.dumps is the path.
    try:
        from qiskit.qasm2 import dumps  # type: ignore

        return dumps(circuit)
    except ImportError:
        pass
    qasm_method = getattr(circuit, "qasm", None)
    if callable(qasm_method):
        return str(qasm_method())
    raise QlyError(
        "Could not convert the circuit to OpenQASM. Install qiskit "
        "(`pip install 'qly-sdk[qiskit]'`) or pass qasm= directly."
    )
