"""device="auto": the SDK passes the receipt through and adds nothing to it.

The two fixtures are real responses from qly.app, captured 2026-09-24: an
accepted route (prefer "price") and the 409 for max_cost_cents=1, which excludes
every route. They are not hand-made, so the shapes here are the shapes the server
sends. The 400 texts are the server's; the SDK must show them unchanged.
"""
import json
import os

import pytest
import responses

from qly import (
    APIError,
    CircuitError,
    Job,
    Qly,
    Routing,
    RoutingRefusedError,
)

BASE = "https://test.local"
HERE = os.path.dirname(os.path.abspath(__file__))


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as f:
        return json.load(f)


def make_client() -> Qly:
    return Qly(api_key="qly_live_test", base_url=BASE)


BELL = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0], q[1];\nmeasure q -> c;\n'


def request_body(call):
    return json.loads(call.request.body)


# -- route(): the receipt -------------------------------------------------------


@responses.activate
def test_route_returns_the_receipt_untouched():
    real = fixture("route_price_ok.json")
    responses.add(responses.POST, f"{BASE}/api/v1/route", json={"routing": real})

    r = make_client().route(BELL, prefer="price")

    assert isinstance(r, Routing)
    assert r.raw == real
    # The sentence is the server's, shown as sent.
    assert r.because.text == real["because"]["text"]
    assert r.chosen.provider == real["chosen"]["provider"]
    assert r.chosen.device_name == real["chosen"]["device_name"]
    assert len(r.candidates) == len(real["candidates"])


@responses.activate
def test_route_sends_only_what_was_given():
    responses.add(responses.POST, f"{BASE}/api/v1/route", json={"routing": fixture("route_price_ok.json")})
    make_client().route(BELL, prefer="price")
    body = request_body(responses.calls[0])
    assert body["device"] == "auto"
    assert body["prefer"] == "price"
    for absent in ("providers", "exclude", "max_cost_cents", "fallback", "provider"):
        assert absent not in body, f"{absent} was sent though the caller did not give it"


@responses.activate
def test_route_passes_every_option_through_unchanged():
    responses.add(responses.POST, f"{BASE}/api/v1/route", json={"routing": fixture("route_price_ok.json")})
    make_client().route(
        BELL, prefer="price", providers=["ibm", "aws"], exclude=["ibm-fez"], max_cost_cents=500, fallback="none", shots=256
    )
    body = request_body(responses.calls[0])
    assert body["providers"] == ["ibm", "aws"]
    assert body["exclude"] == ["ibm-fez"]
    assert body["max_cost_cents"] == 500
    assert body["fallback"] == "none"
    assert body["shots"] == 256


@responses.activate
def test_a_missing_prefer_is_the_servers_to_refuse_not_the_clients():
    """No default, no client-side check: the user sees the server's own sentence."""
    text = 'device "auto" needs prefer: "price", "queue" or "quality".'
    responses.add(responses.POST, f"{BASE}/api/v1/route", json={"error": text}, status=400)
    with pytest.raises(CircuitError) as exc:
        make_client().route(BELL)
    assert str(exc.value) == text
    assert "prefer" not in request_body(responses.calls[0])


@responses.activate
def test_route_accepts_a_qiskit_circuit():
    qiskit = pytest.importorskip("qiskit")
    qc = qiskit.QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    responses.add(responses.POST, f"{BASE}/api/v1/route", json={"routing": fixture("route_price_ok.json")})
    make_client().route(circuit=qc, prefer="price")
    assert "OPENQASM" in request_body(responses.calls[0])["qasm"]


def test_route_rejects_both_qasm_and_circuit():
    with pytest.raises(ValueError):
        make_client().route(BELL, circuit=object(), prefer="price")


# -- the receipt's own rules, on real data --------------------------------------


def test_eligible_is_exactly_not_excluded_and_a_value_is_present_exactly_when_eligible():
    r = Routing.from_json(fixture("route_price_ok.json"))
    assert r.candidates, "fixture has candidates"
    for c in r.candidates:
        assert c.eligible == (c.excluded is None), c.machine
        assert (c.value is not None) == c.eligible, c.machine


def test_an_estimate_carries_its_basis_and_range_and_an_exact_price_does_not():
    r = Routing.from_json(fixture("route_price_ok.json"))
    estimates = [c for c in r.candidates if c.eligible and c.basis == "estimate"]
    exact = [c for c in r.candidates if c.eligible and c.basis == "exact"]
    assert estimates and exact
    for c in estimates:
        assert c.range_cents is not None and c.range_cents[0] <= c.value <= c.range_cents[1]
    for c in exact:
        assert c.range_cents is None


def test_vendor_billed_is_a_fact_kept_apart_from_the_price():
    r = Routing.from_json(fixture("route_price_ok.json"))
    with_fact = [c for c in r.candidates if c.vendor_billed]
    assert with_fact, "the fixture has an IBM candidate with vendor_billed"
    for c in with_fact:
        assert set(c.vendor_billed) >= {"seconds", "jobs", "from", "to"}
        # Not a price: the price is `value`, in cents, and does not come from it.
        assert c.value is not None


def test_the_sdk_adds_no_score_rank_or_best():
    r = Routing.from_json(fixture("route_price_ok.json"))
    for name in ("score", "rank", "best"):
        assert not hasattr(r, name)
        assert not hasattr(r.chosen, name)
        assert not hasattr(r.because, name)
        for c in r.candidates:
            assert not hasattr(c, name)


def test_the_sdk_does_not_reorder_candidates():
    real = fixture("route_price_ok.json")
    r = Routing.from_json(real)
    assert [c.machine for c in r.candidates] == [c["machine"] for c in real["candidates"]]


def test_absent_is_none_and_requested_none_means_not_specified():
    r = Routing.from_json(fixture("route_price_ok.json"))
    assert r.requested.providers is None
    assert r.requested.exclude is None
    assert r.requested.max_cost_cents is None
    assert r.chosen.initial_layout is None  # default placement, not "unknown"
    assert Routing.from_json({"requested": {}, "candidates": []}).chosen is None
    assert Routing.from_json(None) is None


# -- prefer="queue": a different unit, and the price kept apart ---------------------


def test_a_queue_receipt_says_what_its_value_is_and_keeps_the_price_separate():
    """Captured from qly.app 2026-09-24. value is tasks queued, not cents."""
    r = Routing.from_json(fixture("route_queue_ok.json"))
    assert r.requested.prefer == "queue"
    assert r.requested.fallback is None  # not sent, so not specified
    eligible = [c for c in r.candidates if c.eligible]
    assert eligible
    for c in eligible:
        assert c.unit == "tasks" and c.basis == "reported"
        # The charge that breaks a tie is its own field, never folded into value.
        assert c.price_cents is not None and c.price_basis
    for c in r.candidates:
        assert c.eligible == (c.excluded is None)
        if not c.eligible:
            assert c.value is None and c.price_cents is None
    # The server's sentence names the tie-break; it is shown as sent.
    assert r.because.text == fixture("route_queue_ok.json")["because"]["text"]


def test_a_price_receipt_has_no_queue_fields():
    r = Routing.from_json(fixture("route_price_ok.json"))
    assert all(c.price_cents is None and c.price_basis is None for c in r.candidates)


# -- prefer="quality" ---------------------------------------------------------------
#
# Captured from qly.app 2026-09-24. Quality refuses today, for every circuit, and
# says why per candidate. No response has yet carried the `measured` evidence
# object, so it is deliberately not typed; when present it is in Candidate.raw.


@pytest.mark.parametrize(
    "name, code",
    [("route_quality_refused_2q.json", "no_current_measurements"), ("route_quality_refused_4q.json", "width_not_measured")],
)
def test_a_quality_refusal_carries_one_reason_per_candidate_in_the_servers_words(name, code):
    real = fixture(name)
    e = None
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, f"{BASE}/api/v1/route", json=real, status=409)
        with pytest.raises(RoutingRefusedError) as exc:
            make_client().route(BELL, prefer="quality")
        e = exc.value
    assert e.routing.requested.prefer == "quality"
    assert e.routing.chosen is None and e.routing.because is None
    assert all(not c.eligible and c.excluded and c.excluded.text for c in e.routing.candidates)
    assert code in {c.excluded.code for c in e.routing.candidates}
    # Verbatim: the SDK does not summarise, translate or merge reasons.
    for c, raw in zip(e.routing.candidates, real["routing"]["candidates"]):
        assert c.excluded.text == raw["excluded"]["text"]


# -- refusal ---------------------------------------------------------------------


@responses.activate
def test_a_routing_refusal_is_its_own_exception_carrying_the_whole_receipt():
    real = fixture("route_refused.json")
    responses.add(responses.POST, f"{BASE}/api/v1/route", json=real, status=409)

    with pytest.raises(RoutingRefusedError) as exc:
        make_client().route(BELL, prefer="price", max_cost_cents=1)

    e = exc.value
    assert e.status_code == 409
    assert str(e) == real["error"]
    assert e.routing.chosen is None and e.routing.because is None
    assert len(e.routing.candidates) == len(real["routing"]["candidates"])
    for c in e.routing.candidates:
        assert not c.eligible
        assert c.excluded.code and c.excluded.text
    assert e.payload == real


@responses.activate
def test_a_routing_refusal_is_not_a_circuit_error_but_is_an_api_error():
    responses.add(responses.POST, f"{BASE}/api/v1/route", json=fixture("route_refused.json"), status=409)
    with pytest.raises(APIError) as exc:
        make_client().route(BELL, prefer="price", max_cost_cents=1)
    assert isinstance(exc.value, RoutingRefusedError)
    assert not isinstance(exc.value, CircuitError)


@responses.activate
def test_a_409_without_the_routing_code_is_an_ordinary_api_error():
    responses.add(responses.POST, f"{BASE}/api/v1/jobs", json={"error": "conflict"}, status=409)
    with pytest.raises(APIError) as exc:
        make_client().submit(BELL, provider="clifft", device="clifft-sim")
    assert not isinstance(exc.value, RoutingRefusedError)


# -- the server's rejected combinations reach the user unchanged --------------------


@responses.activate
@pytest.mark.parametrize(
    "text",
    [
        'prefer must be "price", "queue" or "quality" (got "bogus").',
        'provider cannot be combined with device "auto". To restrict which providers are considered, pass providers: ["ibm", ...].',
        'fallback "next" is not available yet. Omit fallback, or pass fallback: "none".',
        'prefer, providers, exclude, max_cost_cents and fallback only apply with device "auto".',
        'device "auto" needs the circuit as qasm (OpenQASM), so Qly can see how many qubits it needs.',
    ],
)
def test_the_servers_400_text_is_shown_verbatim(text):
    responses.add(responses.POST, f"{BASE}/api/v1/route", json={"error": text}, status=400)
    with pytest.raises(CircuitError) as exc:
        make_client().route(BELL, prefer="queue")
    assert str(exc.value) == text


# -- submit with device="auto" -----------------------------------------------------


@responses.activate
def test_submit_auto_sends_no_provider_and_only_the_given_routing_fields():
    responses.add(
        responses.POST, f"{BASE}/api/v1/jobs",
        json={"id": "j1", "provider": "aws", "device": "arn:x", "device_name": "X", "status": "QUEUED", "routing": fixture("route_price_ok.json")},
        status=201,
    )
    job = make_client().submit(BELL, device="auto", prefer="price")
    body = request_body(responses.calls[0])
    assert body["device"] == "auto" and body["prefer"] == "price"
    for absent in ("provider", "providers", "exclude", "max_cost_cents", "fallback"):
        assert absent not in body
    # The resolved device, not "auto"; only routing.requested says auto.
    assert job.device == "arn:x"
    assert job.routing.requested.device == "auto"
    assert job.routing.because.text


@responses.activate
def test_run_passes_the_routing_fields_through_to_submit():
    responses.add(responses.POST, f"{BASE}/api/v1/jobs", json={"id": "j1", "status": "COMPLETED", "done": True}, status=201)
    responses.add(responses.GET, f"{BASE}/api/v1/jobs/j1", json={"id": "j1", "status": "COMPLETED", "done": True})
    make_client().run(BELL, device="auto", prefer="price", max_cost_cents=300)
    body = request_body(responses.calls[0])
    assert body["prefer"] == "price" and body["max_cost_cents"] == 300 and "provider" not in body


@responses.activate
def test_a_named_device_still_sends_its_provider_and_no_routing_fields():
    responses.add(responses.POST, f"{BASE}/api/v1/jobs", json={"id": "j1", "status": "QUEUED"}, status=201)
    make_client().submit(BELL, provider="clifft", device="clifft-sim")
    body = request_body(responses.calls[0])
    assert body["provider"] == "clifft"
    for absent in ("prefer", "providers", "exclude", "max_cost_cents", "fallback"):
        assert absent not in body


@responses.activate
def test_a_routing_refusal_on_submit_carries_the_receipt():
    responses.add(responses.POST, f"{BASE}/api/v1/jobs", json=fixture("route_refused.json"), status=409)
    with pytest.raises(RoutingRefusedError) as exc:
        make_client().submit(BELL, device="auto", prefer="price", max_cost_cents=1)
    assert exc.value.routing.candidates


# -- Job.routing ---------------------------------------------------------------------


def test_job_routing_is_none_when_the_job_was_not_auto_routed():
    assert Job.from_json({"id": "j", "status": "COMPLETED"}).routing is None


def test_job_routing_reads_the_stored_receipt():
    job = Job.from_json({"id": "j", "status": "COMPLETED", "routing": fixture("route_price_ok.json")})
    assert isinstance(job.routing, Routing)
    assert job.routing.chosen.machine


def test_a_failed_submit_body_that_carries_routing_is_still_readable():
    """On an upstream failure the server returns the error AND the receipt."""
    body = {"error": "provider down", "routing": fixture("route_price_ok.json")}
    assert Routing.from_json(body["routing"]).attempts is not None
