# qly-sdk

Python client for [Qly](https://qly.app), a quantum computing platform. Write a
circuit in OpenQASM or Qiskit, submit it to real quantum hardware (IBM, IonQ,
AWS Braket, Quantinuum, Azure) or a simulator, and pull the results back.

```bash
pip install qly-sdk
```

The PyPI name is `qly-sdk`; everything else is just `qly` — you `import qly`
and the CLI command is `qly`.

## Getting a key

Sign in at [qly.app](https://qly.app), open **API Keys** (`/settings/api-keys`),
and create one. You'll see the secret once — it looks like `qly_live_…`. Jobs you
run with the key are billed to your account's prepaid balance, which you top up
on the billing page.

## Quickstart

```python
from qly import Qly

client = Qly(api_key="qly_live_...")   # or set QLY_API_KEY in your environment

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

job = client.run(bell, provider="ibm", device="ibm_kingston", shots=1024)
print(job.counts)   # {'00': 503, '11': 521}
```

`run()` submits and blocks until the job finishes. If you'd rather not block,
use `submit()` and poll yourself.

## Command line

The package installs a `qly` command, so you can work from a shell without
writing any Python:

```bash
qly configure                  # paste your key once; stored in ~/.config/qly/
qly devices                    # what can I run on?
qly balance

qly submit bell.qasm --provider ionq --device simulator --shots 1024 --wait
qly jobs --limit 10            # recent jobs
qly job <job-id>               # status + measurement histogram
```

`submit --wait` polls until the job finishes and prints the counts. Every
command takes `--json` for machine-readable output, and `--api-key` /
`QLY_API_KEY` override the stored key (useful in CI).

## Submitting and polling separately

```python
job = client.submit(bell, provider="ibm", device="ibm_kingston", shots=1024)
print(job.id, job.status)        # 'd4a…', 'PENDING'

job = client.wait(job)           # blocks until terminal, raises on failure
print(job.counts)

# or poll by hand:
job = client.get_job(job.id)
if job.done:
    print(job.results)
```

## From a Qiskit circuit

Install the extra (`pip install "qly-sdk[qiskit]"`) and pass the circuit directly:

```python
from qiskit import QuantumCircuit
from qly import Qly

qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure([0, 1], [0, 1])

client = Qly()
job = client.run(circuit=qc, provider="ionq", device="simulator", shots=512)
print(job.counts)
```

## Listing devices and checking balance

```python
for d in client.devices():
    print(d.provider, d.id, d.qubits, "sim" if d.is_simulator else "qpu")

print(client.balance().formatted)   # '$12.40'
```

## Estimator (expectation values)

For IBM, you can ask for Pauli expectation values instead of shot counts:

```python
job = client.run(
    ansatz_qasm,
    provider="ibm",
    device="ibm_kingston",
    primitive="estimator",
    observables=["ZZ", "IZ", "ZI"],
    shots=4096,
)
print(job.results)   # {'evs': [...], 'stds': [...]}
```

## Errors

| Exception | When |
|-----------|------|
| `AuthenticationError` | missing / invalid / revoked key |
| `InsufficientBalanceError` | not enough credit; `.estimated_cents`, `.balance_cents` |
| `RateLimitError` | too many submissions; `.retry_after` |
| `JobFailedError` | job ended FAILED/ERROR/CANCELLED; `.job` for detail |
| `JobTimeoutError` | `run()`/`wait()` timed out |
| `APIError` | anything else; `.status_code`, `.payload` |

```python
from qly import InsufficientBalanceError

try:
    client.run(circuit, provider="ibm", device="ibm_kingston")
except InsufficientBalanceError as e:
    print(f"Need ~{e.estimated_cents}¢, have {e.balance_cents}¢")
```

## Configuration

| Argument | Env var | Default |
|----------|---------|---------|
| `api_key` | `QLY_API_KEY` | — (required) |
| `base_url` | `QLY_BASE_URL` | `https://qly.app` |

## License

MIT
