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

qly estimate bell.qasm --provider ionq --device qpu.aria-1 --shots 1000
qly calibration ibm_kingston   # is this device any good right now?
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

## What the device actually did

A finished job carries more than a histogram. `job.run_facts` reports what the
provider measured — and only what it measured: a fact the device did not
publish is `None`, which is not the same as zero.

```python
job = client.run(bell_qasm, provider="ibm", device="ibm_kingston")

f = job.run_facts
print(f.execution_ms)        # time on the device, excluding queueing
print(f.qpu_seconds)         # what you were billed for
print(f.physical_qubits)     # [9, 14] — where the compiler put your circuit
print(f.native_gate_counts)  # {'rz': 1, 'sx': 1, 'ecr': 1}
print(f.two_qubit_gates)     # the usual proxy for how noisy the run will be
print(f.native_qasm)         # the circuit the device really ran
```

## Pricing a run before you pay for it

`estimate()` submits nothing and prices with the same helpers the submit path
bills with, so an estimate and the charge that follows cannot drift apart.

```python
est = client.estimate(bell_qasm, provider="ionq", device="qpu.aria-1", shots=1000)
if est.exact:
    print(est.cost_formatted)       # '$30.00'
else:
    print(est.cost_range_cents)     # per-QPU-second devices can only give a range
    print(est.note)                 # ...and say why
print(est.sufficient_balance)
```

Check `.exact` before treating `.cost_cents` as a price. Per-shot and per-task
billing is known up front; per-QPU-second billing is not, because nobody knows
how long the QPU will hold the circuit until it has.

## Device calibration

`devices()` answers "what can I target". `calibration()` answers "is it any
good right now" — which qubit count alone will not tell you.

```python
cal = client.calibration("ibm_kingston")
print(cal.connectivity)      # 'heavy-hex lattice'
for m in cal.metrics:
    print(m.label, m.value, m.unit or "")
```

`cal.is_live` is False for simulators and for devices whose provider publishes
no calibration; an empty `metrics` list means "not published", not "perfect".

## Errors

| Exception | When |
|-----------|------|
| `AuthenticationError` | missing / invalid / revoked key |
| `InsufficientBalanceError` | not enough credit; `.estimated_cents`, `.balance_cents` |
| `CircuitError` | the request itself was rejected: unparseable QASM, a gate the device lacks, shots out of range. Subclasses `APIError` |
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

`CircuitError` is the one to act on rather than retry: it carries the
provider's own wording, which names the gate or index it rejected, and the same
request will fail identically every time.

```python
from qly import CircuitError

try:
    client.run(circuit, provider="ibm", device="ibm_kingston")
except CircuitError as e:
    print(f"Fix the circuit: {e}")   # "Cannot find gate 'cccx' in the basis"
```

## A worked example

[`examples/bell_pair.py`](examples/bell_pair.py) runs a Bell pair on a free
simulator, then the same circuit on a real QPU — checking calibration and
pricing the run first. The hardware leg is opt-in behind `--hardware`.

```bash
export QLY_API_KEY=qly_live_...
python examples/bell_pair.py              # simulator only
python examples/bell_pair.py --hardware   # also submit to a QPU
```

## Configuration

| Argument | Env var | Default |
|----------|---------|---------|
| `api_key` | `QLY_API_KEY` | — (required) |
| `base_url` | `QLY_BASE_URL` | `https://qly.app` |

## License

MIT
