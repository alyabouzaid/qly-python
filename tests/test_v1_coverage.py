"""Tests for the endpoints and fields added in the /api/v1 coverage pass.

The native OpenQASM below is the shape the QPUs actually return: physical
qubits written `$9` rather than `q[0]`, and a measurement written as an
assignment to a classical bit, which is what made an earlier gate counter
report a phantom "b" gate.
"""

import pytest
import responses

from qly import Calibration, CircuitError, Estimate, InsufficientBalanceError, Qly, RunFacts

BASE = "https://test.local"

NATIVE = """OPENQASM 3.0;
bit[2] b;
rz(1.5707963267948966) $9;
prx(1.5707963267948966, 0.0) $9;
cz $9, $14;
b[0] = measure $9;
b[1] = measure $14;
"""


def make_client() -> Qly:
    return Qly(api_key="qly_live_test", base_url=BASE)


# -- run facts ------------------------------------------------------------


@responses.activate
def test_job_exposes_run_facts():
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs/abc",
        json={
            "id": "abc",
            "status": "COMPLETED",
            "results": {"counts": {"00": 500, "11": 524}},
            "run_facts": {
                "shots": 1024,
                "execution_ms": 12.5,
                "qpu_seconds": 3.25,
                "physical_qubits": [9, 14],
                "native_qasm": NATIVE,
                "native_gate_counts": {"rz": 1, "prx": 1, "cz": 1},
                "measured_qubits": [9, 14],
            },
        },
    )
    facts = make_client().get_job("abc").run_facts
    assert facts.execution_ms == 12.5
    assert facts.qpu_seconds == 3.25
    assert facts.physical_qubits == [9, 14]
    assert facts.native_qasm.startswith("OPENQASM 3.0")
    assert facts.two_qubit_gates == 1


@responses.activate
def test_run_facts_absent_when_provider_reported_nothing():
    """A device that publishes nothing must not look like a device that ran nothing."""
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs/bare",
        json={"id": "bare", "status": "COMPLETED", "results": {"counts": {"0": 10}}},
    )
    facts = make_client().get_job("bare").run_facts
    assert isinstance(facts, RunFacts)
    assert facts.execution_ms is None
    assert facts.qpu_seconds is None
    assert facts.physical_qubits == []
    assert facts.two_qubit_gates == 0


@responses.activate
def test_estimated_qpu_time_is_not_reported_as_measured():
    """Qly's own estimate must not be readable as something the device said."""
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs/est",
        json={
            "id": "est",
            "status": "COMPLETED",
            "run_facts": {"billed_qpu_seconds": 3.25},
        },
    )
    facts = make_client().get_job("est").run_facts
    assert facts.qpu_seconds is None
    assert facts.billed_qpu_seconds == 3.25


def test_two_qubit_gates_counts_known_entanglers_only():
    facts = RunFacts.from_json({"native_gate_counts": {"cz": 14, "ecr": 2, "prx": 40, "rz": 9}})
    assert facts.two_qubit_gates == 16


# -- estimate -------------------------------------------------------------


@responses.activate
def test_estimate_exact_price():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/estimate",
        json={
            "provider": "ionq",
            "device": "qpu.aria-1",
            "device_name": "Aria 1",
            "shots": 1000,
            "billing": "per_shot",
            "exact": True,
            "cost_cents": 3000,
            "cost_usd": 30.0,
            "cost_formatted": "$30.00",
            "sufficient_balance": False,
            "balance_cents": 500,
            "warnings": [],
        },
    )
    est = make_client().estimate(qasm="OPENQASM 2.0;", provider="ionq", device="qpu.aria-1", shots=1000)
    assert isinstance(est, Estimate)
    assert est.exact is True
    assert est.cost_cents == 3000
    assert est.sufficient_balance is False


@responses.activate
def test_estimate_range_when_not_exact():
    """QPU-second billing cannot be priced before the run, and must say so."""
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/estimate",
        json={
            "provider": "ibm",
            "device": "ibm_kingston",
            "exact": False,
            "cost_cents": None,
            "cost_range_cents": {"min": 100, "max": 900},
            "note": "Billed per QPU-second; the exact charge is known once the run finishes.",
        },
    )
    est = make_client().estimate(qasm="OPENQASM 2.0;", provider="ibm", device="ibm_kingston")
    assert est.exact is False
    assert est.cost_cents is None
    assert est.cost_range_cents == {"min": 100, "max": 900}
    assert "QPU-second" in est.note


@responses.activate
def test_estimate_submits_nothing():
    responses.add(responses.POST, f"{BASE}/api/v1/estimate", json={"provider": "ibm", "device": "d"})
    make_client().estimate(qasm="OPENQASM 2.0;", provider="ibm", device="d")
    assert [c.request.url for c in responses.calls] == [f"{BASE}/api/v1/estimate"]


# -- calibration ----------------------------------------------------------


@responses.activate
def test_calibration():
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/calibration",
        json={
            "device": "ibm_kingston",
            "device_name": "Kingston",
            "provider": "ibm",
            "type": "real",
            "qubits": 156,
            "source": "provider",
            "connectivity": "heavy-hex lattice",
            "metrics": [{"label": "Median CZ error", "value": 0.0031}],
        },
    )
    cal = make_client().calibration("ibm_kingston")
    assert isinstance(cal, Calibration)
    assert cal.connectivity == "heavy-hex lattice"
    assert cal.metrics[0].label == "Median CZ error"
    assert cal.is_live is True
    assert responses.calls[0].request.params["device"] == "ibm_kingston"


@responses.activate
def test_calibration_simulator_is_not_live():
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/calibration",
        json={"device": "sim", "source": "simulator", "metrics": [], "note": "Simulators are noiseless"},
    )
    assert make_client().calibration("sim").is_live is False


# -- error messages -------------------------------------------------------


@responses.activate
def test_unsupported_gate_raises_circuit_error_with_provider_wording():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={"error": "Cannot find gate 'cccx' in the device's basis."},
        status=400,
    )
    with pytest.raises(CircuitError) as exc:
        make_client().submit(qasm="OPENQASM 2.0;", provider="ibm", device="ibm_kingston")
    assert "cccx" in str(exc.value)
    assert exc.value.status_code == 400


@responses.activate
def test_shots_out_of_range_is_a_circuit_error_not_a_platform_error():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={"error": "shots must be at least 100 for this device."},
        status=422,
    )
    with pytest.raises(CircuitError):
        make_client().submit(qasm="OPENQASM 2.0;", provider="ionq", device="qpu.aria-1", shots=1)


@responses.activate
def test_out_of_credits_message_says_what_to_do_next():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={
            "error": "Insufficient balance. This job costs ~$30.00. Current balance: $5.00.",
            "estimatedCents": 3000,
            "balanceCents": 500,
        },
        status=402,
    )
    with pytest.raises(InsufficientBalanceError) as exc:
        make_client().submit(qasm="OPENQASM 2.0;", provider="ionq", device="qpu.aria-1")
    message = str(exc.value)
    assert "Insufficient balance" in message
    assert "qly.app/pricing" in message
    assert exc.value.estimated_cents == 3000
    assert exc.value.balance_cents == 500


def test_circuit_error_is_catchable_as_api_error():
    """Existing `except APIError` code must keep working."""
    from qly import APIError

    assert issubclass(CircuitError, APIError)
