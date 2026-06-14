from qly.models import Balance, Device, Job, devices_from_json, jobs_from_json


def test_device_from_json_and_simulator_flag():
    d = Device.from_json(
        {"provider": "ibm", "id": "ibm_kingston", "name": "IBM Kingston", "qubits": 156, "type": "real"}
    )
    assert d.provider == "ibm"
    assert d.qubits == 156
    assert d.is_simulator is False

    sim = Device.from_json({"id": "sv1", "name": "SV1", "qubits": 34, "type": "simulator"})
    assert sim.is_simulator is True


def test_balance_from_json():
    b = Balance.from_json({"balance_cents": 1240, "balance_usd": 12.4, "balance_formatted": "$12.40"})
    assert b.cents == 1240
    assert b.usd == 12.4
    assert b.formatted == "$12.40"


def test_balance_derives_usd_when_missing():
    b = Balance.from_json({"balance_cents": 500})
    assert b.usd == 5.0


def test_job_status_helpers():
    pending = Job.from_json({"id": "j1", "status": "PENDING"})
    assert not pending.done
    assert not pending.succeeded
    assert not pending.failed

    done = Job.from_json({"id": "j2", "status": "COMPLETED", "results": {"counts": {"00": 10}}})
    assert done.done
    assert done.succeeded
    assert done.counts == {"00": 10}

    failed = Job.from_json({"id": "j3", "status": "FAILED"})
    assert failed.done
    assert failed.failed
    assert failed.counts is None


def test_job_done_respects_explicit_flag():
    job = Job.from_json({"id": "j4", "status": "WEIRD", "done": True})
    assert job.done is True


def test_collection_parsers():
    devices = devices_from_json({"devices": [{"id": "a", "name": "A", "qubits": 5, "type": "real"}]})
    assert len(devices) == 1
    jobs = jobs_from_json({"jobs": [{"id": "x", "status": "QUEUED"}]})
    assert jobs[0].id == "x"
