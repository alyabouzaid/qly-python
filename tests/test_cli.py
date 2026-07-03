"""CLI tests. HTTP is mocked with `responses`; the config file is redirected
into a temp dir via the QLY_CONFIG env var so tests never touch ~/.config."""

import json

import responses

from qly.cli import main

BASE = "https://qly.app"


def _use_tmp_config(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    monkeypatch.setenv("QLY_CONFIG", str(config))
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    monkeypatch.delenv("QLY_BASE_URL", raising=False)
    return config


def test_configure_writes_key(monkeypatch, tmp_path, capsys):
    config = _use_tmp_config(monkeypatch, tmp_path)
    assert main(["configure", "--api-key", "qly_live_abc"]) == 0
    saved = json.loads(config.read_text())
    assert saved["api_key"] == "qly_live_abc"
    assert str(config) in capsys.readouterr().out


def test_no_key_is_a_clean_error(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    assert main(["balance"]) == 1
    assert "qly configure" in capsys.readouterr().err


@responses.activate
def test_devices_table(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/devices",
        json={
            "devices": [
                {
                    "provider": "ionq",
                    "id": "simulator",
                    "name": "IonQ Simulator",
                    "qubits": 29,
                    "type": "simulator",
                    "status": "online",
                }
            ]
        },
    )
    assert main(["devices", "--api-key", "qly_live_abc"]) == 0
    out = capsys.readouterr().out
    assert "IonQ Simulator" in out
    assert "PROVIDER" in out


@responses.activate
def test_devices_provider_filter(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/devices",
        json={
            "devices": [
                {"provider": "ionq", "id": "simulator", "name": "IonQ Sim", "qubits": 29,
                 "type": "simulator", "status": "online"},
                {"provider": "ibm", "id": "ibm_kingston", "name": "IBM Kingston", "qubits": 156,
                 "type": "real", "status": "online"},
            ]
        },
    )
    assert main(["devices", "--api-key", "qly_live_abc", "--provider", "ibm", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [d["id"] for d in listed] == ["ibm_kingston"]


@responses.activate
def test_balance(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/balance",
        json={"balance_cents": 1234, "balance_usd": 12.34, "balance_formatted": "$12.34"},
    )
    assert main(["balance", "--api-key", "qly_live_abc"]) == 0
    assert capsys.readouterr().out.strip() == "$12.34"


@responses.activate
def test_submit_reads_file_and_waits(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    qasm_file = tmp_path / "bell.qasm"
    qasm_file.write_text('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\n')

    responses.add(
        responses.POST,
        f"{BASE}/api/v1/jobs",
        json={"id": "job_1", "provider": "ionq", "status": "QUEUED", "shots": 100},
        status=201,
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs/job_1",
        json={
            "id": "job_1",
            "provider": "ionq",
            "status": "COMPLETED",
            "done": True,
            "results": {"counts": {"00": 52, "11": 48}},
        },
    )
    code = main(
        [
            "submit", str(qasm_file),
            "--api-key", "qly_live_abc",
            "--provider", "ionq", "--device", "simulator",
            "--shots", "100", "--wait", "--json",
        ]
    )
    assert code == 0
    body = json.loads(responses.calls[0].request.body)
    assert body["qasm"].startswith("OPENQASM 2.0;")
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "COMPLETED"
    assert summary["counts"] == {"00": 52, "11": 48}


@responses.activate
def test_job_failure_sets_exit_code(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs/job_bad",
        json={"id": "job_bad", "status": "FAILED", "error": "device offline"},
    )
    assert main(["job", "job_bad", "--api-key", "qly_live_abc"]) == 1
    assert "device offline" in capsys.readouterr().out


@responses.activate
def test_jobs_list(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/jobs",
        json={
            "jobs": [
                {"id": "job_2", "provider": "ibm", "device": "ibm_kingston",
                 "status": "COMPLETED", "shots": 1024, "created_at": "2026-07-01T00:00:00Z"}
            ]
        },
    )
    assert main(["jobs", "--api-key", "qly_live_abc"]) == 0
    assert "job_2" in capsys.readouterr().out


@responses.activate
def test_api_error_is_reported(monkeypatch, tmp_path, capsys):
    _use_tmp_config(monkeypatch, tmp_path)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/balance",
        json={"error": "Invalid API key"},
        status=401,
    )
    assert main(["balance", "--api-key", "qly_live_bad"]) == 1
    assert "Invalid API key" in capsys.readouterr().err


def test_config_key_used_when_no_flag(monkeypatch, tmp_path, capsys):
    config = _use_tmp_config(monkeypatch, tmp_path)
    main(["configure", "--api-key", "qly_live_from_config"])
    capsys.readouterr()

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            f"{BASE}/api/v1/balance",
            json={"balance_cents": 0, "balance_usd": 0, "balance_formatted": "$0.00"},
        )
        assert main(["balance"]) == 0
        auth = rsps.calls[0].request.headers["Authorization"]
    assert auth == "Bearer qly_live_from_config"
    assert config.exists()
