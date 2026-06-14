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
    InsufficientBalanceError,
    JobFailedError,
    JobTimeoutError,
    QlyError,
    RateLimitError,
)
from .models import Balance, Device, Job, devices_from_json, jobs_from_json

DEFAULT_BASE_URL = "https://qly.app"
_USER_AGENT = "qly-python/0.1.0"


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
            raise InsufficientBalanceError(message, estimated_cents=est, balance_cents=bal)
        if status == 429:
            retry = None
            if isinstance(payload, dict):
                retry = payload.get("retryAfterSeconds")
            raise RateLimitError(message, retry_after=retry)
        raise APIError(message, status_code=status, payload=payload)

    # -- Devices & balance --------------------------------------------------

    def devices(self) -> List[Device]:
        """List the quantum devices available to your account."""
        return devices_from_json(self._request("GET", "/api/v1/devices"))

    def balance(self) -> Balance:
        """Return your current prepaid credit balance."""
        return Balance.from_json(self._request("GET", "/api/v1/balance"))

    # -- Jobs ---------------------------------------------------------------

    def submit(
        self,
        qasm: Optional[str] = None,
        *,
        provider: str,
        device: str,
        shots: int = 1024,
        primitive: str = "sampler",
        observables: Optional[List[str]] = None,
        circuit: Optional[Any] = None,
        qiskit: Optional[str] = None,
        ionq_native: Optional[Dict[str, Any]] = None,
        device_name: Optional[str] = None,
    ) -> Job:
        """Submit a circuit and return immediately with a queued :class:`Job`.

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
            "provider": provider,
            "device": device,
            "shots": shots,
            "primitive": primitive,
            "observables": observables or [],
        }
        if qasm is not None:
            body["qasm"] = qasm
        if qiskit is not None:
            body["qiskit"] = qiskit
        if ionq_native is not None:
            body["ionq_native"] = ionq_native
        if device_name is not None:
            body["device_name"] = device_name

        return Job.from_json(self._request("POST", "/api/v1/jobs", json=body))

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
        "(`pip install qly[qiskit]`) or pass qasm= directly."
    )
