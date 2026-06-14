import json

import pytest
import responses

from qly import (
    APIError,
    AuthenticationError,
    InsufficientBalanceError,
    JobFailedError,
    Qly,
    RateLimitError,
)

BASE = "https://test.local"


def make_client() -> Qly:
    return Qly(api_key="qly_live_test", base_url=BASE)


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    with pytest.raises(AuthenticationError):
        Qly()


def test_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("QLY_API_KEY", "qly_live_fromenv")
    client = Qly(base_url=BASE)
    assert client.api_key == "qly_live_fromenv"


def test_sends_bearer_header():
    client = make_client()
    assert client._session.headers["Authorization"] == "Bearer qly_live_test"


@responses.activate
def test_devices():
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/devices",
        json={"devices": [{"provider": "ibm", "id": "ibm_kingston", "name": "Kingston", "qubits": 156, "type": "real"}]},
    )
    devices = make_client().devices()
    assert devices[0].id == "ibm_kingston"
    assert devices[0].qubits == 156


@responses.activate
def test_balance():
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/balance",
        json={"balance_cents": 1000, "balance_usd": 10.0, "balance_formatted": "$10.00"},
    )
    assert make_client().balance().cents == 1000


@responses.activate
def test_submit_sends_expected_body():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={"id": "job123", "provider": "ibm", "device": "ibm_kingston", "status": "PENDING", "shots": 1024},
        status=201,
    )
    job = make_client().submit("OPENQASM 2.0;", provider="ibm", device="ibm_kingston", shots=1024)
    assert job.id == "job123"
    sent = json.loads(responses.calls[0].request.body)
    assert sent["qasm"] == "OPENQASM 2.0;"
    assert sent["provider"] == "ibm"
    assert sent["device"] == "ibm_kingston"
    assert sent["shots"] == 1024


def test_submit_rejects_missing_circuit():
    with pytest.raises(ValueError):
        make_client().submit(provider="ibm", device="ibm_kingston")


def test_submit_estimator_requires_observables():
    with pytest.raises(ValueError):
        make_client().submit("OPENQASM 2.0;", provider="ibm", device="ibm_kingston", primitive="estimator")


@responses.activate
def test_run_polls_until_done():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={"id": "j", "status": "PENDING"},
        status=201,
    )
    responses.add(responses.GET, f"{BASE}/api/v1/jobs/j", json={"id": "j", "status": "PENDING"})
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs/j",
        json={"id": "j", "status": "COMPLETED", "results": {"counts": {"00": 5, "11": 5}}},
    )
    job = make_client().run("OPENQASM 2.0;", provider="ibm", device="ibm_kingston", poll_interval=0)
    assert job.succeeded
    assert job.counts == {"00": 5, "11": 5}


@responses.activate
def test_run_raises_on_failure():
    responses.add(responses.POST, f"{BASE}/api/v1/jobs", json={"id": "j", "status": "PENDING"}, status=201)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs/j",
        json={"id": "j", "status": "FAILED", "error": {"reason": "boom"}},
    )
    with pytest.raises(JobFailedError) as exc:
        make_client().run("OPENQASM 2.0;", provider="ibm", device="ibm_kingston", poll_interval=0)
    assert exc.value.job.status == "FAILED"


@responses.activate
def test_auth_error_mapping():
    responses.add(responses.GET, f"{BASE}/api/v1/balance", json={"error": "Invalid or revoked API key."}, status=401)
    with pytest.raises(AuthenticationError):
        make_client().balance()


@responses.activate
def test_insufficient_balance_mapping():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={"error": "Insufficient balance.", "estimatedCents": 320, "balanceCents": 100},
        status=402,
    )
    with pytest.raises(InsufficientBalanceError) as exc:
        make_client().submit("OPENQASM 2.0;", provider="ibm", device="ibm_kingston")
    assert exc.value.estimated_cents == 320
    assert exc.value.balance_cents == 100


@responses.activate
def test_rate_limit_mapping():
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={"error": "Too many requests.", "retryAfterSeconds": 42},
        status=429,
    )
    with pytest.raises(RateLimitError) as exc:
        make_client().submit("OPENQASM 2.0;", provider="ibm", device="ibm_kingston")
    assert exc.value.retry_after == 42


@responses.activate
def test_generic_api_error():
    responses.add(responses.GET, f"{BASE}/api/v1/devices", json={"error": "boom"}, status=500)
    with pytest.raises(APIError) as exc:
        make_client().devices()
    assert exc.value.status_code == 500
