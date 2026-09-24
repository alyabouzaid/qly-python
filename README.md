# qly-sdk

Python client for [Qly](https://qly.app), a quantum computing platform. Write a
circuit in OpenQASM or Qiskit, submit it to real quantum hardware (IBM, IonQ,
AWS Braket, Quantinuum, Azure) or a simulator, and pull the results back.

```bash
pip install qly-sdk
export QLY_API_KEY=qly_live_...   # the key from your API Keys page
```

The PyPI name is `qly-sdk`; everything else is just `qly` — you `import qly`
and the CLI command is `qly`.

### In Google Colab

Install with `!pip` and ask for the key when the cell runs, so it never sits in
the notebook text you might share. Add the `[qiskit]` extra if you will pass
Qiskit circuits.

```python
!pip install -q "qly-sdk[qiskit]"

import getpass, os
os.environ["QLY_API_KEY"] = getpass.getpass("Qly API key: ")
```

## Getting a key

Sign in at [qly.app](https://qly.app), open **API Keys** (`/settings/api-keys`),
and create one. You'll see the secret once — it looks like `qly_live_…`. Jobs you
run with the key are billed to your account's prepaid balance, which you top up
on the billing page.

## Quickstart

```python
from qly import Qly

client = Qly()   # reads QLY_API_KEY

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

job = client.run(bell, provider="clifft", device="clifft-sim", shots=1024)
print(job.counts)   # {'00': 503, '11': 521}
```

`run()` submits and blocks until the job finishes. If you'd rather not block,
use `submit()` and poll yourself.

For real hardware, change `provider` and `device` to an id from
`client.devices()`. Real QPUs queue and cost credit; simulators are free and the
code path is identical, so iterate on a simulator first.

Two free simulators are listed. `clifft-sim` draws random shots, so counts vary
between runs like a real device, and it accepts any gate. `ionq` / `simulator`
reports the ideal probabilities scaled to your shot count, so repeated runs
match exactly, and it accepts a smaller gate set: anything it cannot run is
refused with a message naming the instruction rather than skipped.

## Command line

The package installs a `qly` command, so you can work from a shell without
writing any Python:

```bash
export QLY_API_KEY=qly_live_...

cat > bell.qasm <<'EOF'
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
EOF

qly devices                    # what can I run on?
qly balance
qly submit bell.qasm --provider clifft --device clifft-sim --shots 1024 --wait
qly jobs --limit 10            # recent jobs
qly job <job-id>               # status + histogram; the id is printed by submit

qly estimate bell.qasm --provider clifft --device clifft-sim --shots 1024
qly calibration clifft-sim     # live calibration where a provider publishes it
```

`submit --wait` polls until the job finishes and prints the counts. Every
command takes `--json` for machine-readable output, and `--api-key` /
`QLY_API_KEY` override the stored key (useful in CI). `qly configure` stores the
key in `~/.config/qly/` from a terminal; it prompts for the key, so in a
notebook use the environment variable instead.

## Submitting and polling separately

```python
from qly import Qly

client = Qly()

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

job = client.submit(bell, provider="clifft", device="clifft-sim", shots=1024)
print(job.id, job.status)        # a QPU that is still queued shows 'PENDING' here

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

# Four qubits, each in an equal superposition, all measured: 16 outcomes.
qc = QuantumCircuit(4, 4)
qc.h(range(4))
qc.measure(range(4), range(4))

client = Qly()
job = client.run(circuit=qc, provider="clifft", device="clifft-sim", shots=1024)
print(job.counts)   # 16 four-bit strings, about 64 shots each
```

## Listing devices and checking balance

```python
from qly import Qly

client = Qly()

for d in client.devices():
    print(d.provider, d.id, d.qubits, "sim" if d.is_simulator else "qpu")

print(client.balance().formatted)   # '$5.00'
```

## Estimator (expectation values)

For IBM, you can ask for Pauli expectation values instead of shot counts:

```python
from qly import Qly

client = Qly()

ansatz_qasm = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
ry(0.6) q[0];
cx q[0], q[1];
"""

job = client.run(
    ansatz_qasm,
    provider="ibm",
    device="ibm_marrakesh",   # any IBM id from client.devices()
    primitive="estimator",
    observables=["ZZ", "IZ", "ZI"],
    shots=512,
)
print(job.results)   # {'evs': [...], 'stds': [...]}
```

## What the device actually did

A finished job carries more than a histogram. `job.run_facts` reports what the
provider measured — and only what it measured: a fact the device did not
publish is `None`, which is not the same as zero.

```python
from qly import Qly

client = Qly()

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

# A real QPU: this spends credit. On a simulator most of these are None.
job = client.run(bell, provider="ibm", device="ibm_marrakesh", shots=1024)

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
from qly import Qly

client = Qly()

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

est = client.estimate(bell, provider="ibm", device="ibm_marrakesh", shots=1024)
if est.exact:
    print(est.cost_formatted)       # e.g. '$0.82'
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
from qly import Qly

client = Qly()

cal = client.calibration("ibm_marrakesh")
print(cal.connectivity)      # 'heavy-hex lattice'
for m in cal.metrics:
    print(m.label, m.value, m.unit or "")
```

`cal.is_live` is False for simulators and for devices whose provider publishes
no calibration; an empty `metrics` list means "not published", not "perfect".

## Letting Qly choose the machine

Pass `device="auto"` and one preference instead of a device. Qly picks the machine
and returns a receipt saying what it compared and why, with a sentence written by
the server. `route()` shows the decision without submitting or charging anything.

```python
from qly import Qly

client = Qly()

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

routing = client.route(bell, prefer="price")
print(routing.because.text)                  # the server's sentence, as sent
print(routing.chosen.device_name)

for c in routing.candidates:                  # every route, in the order the server sent
    if c.excluded:
        print(c.machine, "excluded:", c.excluded.text)
    else:
        print(c.machine, c.value, c.unit, c.basis)
```

To submit, use the same arguments on `run()` or `submit()`. This one runs on a real
QPU and spends credit, so it is not run in the example above:

```python
from qly import Qly

client = Qly()

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

job = client.run(bell, device="auto", prefer="price")
print(job.device, job.routing.because.text)   # the resolved machine, and why
```

Things the receipt does and does not say:

- `prefer` is required and has no default. The server says which preferences it
  accepts at the moment, and which it does not yet, in its error message.
- Nothing is scored or ranked, and there is no "best". The candidates are ordered
  by the quantity you asked for, for this request only.
- `basis` says whether a price is `"exact"` or an `"estimate"` (with `range_cents`).
  `vendor_billed` is a fact about what the vendor billed Qly for jobs like this,
  never a price.
- `None` means the server did not send the field. Inside `routing.requested`,
  `None` means you did not specify it.
- Leave `provider` out; use `providers=[...]` to restrict which are considered.
  `initial_layout` cannot be combined with `device="auto"`.

If no route is eligible, `route()` and `run()` raise `RoutingRefusedError`, whose
`.routing` is the whole receipt: every candidate and the reason it was excluded.

## Errors

| Exception | When |
|-----------|------|
| `AuthenticationError` | missing / invalid / revoked key |
| `InsufficientBalanceError` | not enough credit; `.estimated_cents`, `.balance_cents` |
| `CircuitError` | the request itself was rejected: unparseable QASM, a gate the device lacks, shots out of range. Subclasses `APIError` |
| `RoutingRefusedError` | `device="auto"` found no eligible route; `.routing` is the receipt with every candidate's reason. Subclasses `APIError` |
| `RateLimitError` | too many submissions; `.retry_after` |
| `JobFailedError` | job ended FAILED/ERROR/CANCELLED; `.job` for detail |
| `JobTimeoutError` | `run()`/`wait()` timed out |
| `APIError` | anything else; `.status_code`, `.payload` |

```python
from qly import Qly, InsufficientBalanceError

client = Qly()

bell = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""

try:
    client.run(bell, provider="ibm", device="ibm_marrakesh", shots=1024)
except InsufficientBalanceError as e:
    print(f"Need ~{e.estimated_cents}¢, have {e.balance_cents}¢")
```

`CircuitError` is the one to act on rather than retry: it carries the
provider's own wording, which names the gate or index it rejected, and the same
request will fail identically every time.

```python
from qly import Qly, CircuitError

client = Qly()

# `crx` is not something the IonQ simulator can run; the refusal names it.
qasm = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
crx(pi/2) q[0], q[1];
measure q -> c;
"""

try:
    client.run(qasm, provider="ionq", device="simulator", shots=100)
except CircuitError as e:
    print(f"Fix the circuit: {e}")   # "... cannot run an instruction in this circuit: `crx(pi/2) q[0],q[1];` ..."
```

## A worked example

[`examples/bell_pair.py`](examples/bell_pair.py) runs a Bell pair on a free
simulator, then the same circuit on a real QPU — checking calibration and
pricing the run first. The hardware leg is opt-in behind `--hardware` and needs
`--device`: the script does not pick a machine for you, because queues and each
machine's readings change; choose from `client.devices()` or qly.app/router.

```bash
export QLY_API_KEY=qly_live_...
python examples/bell_pair.py              # simulator only
python examples/bell_pair.py --hardware --device ibm_marrakesh   # also submit to a QPU
```

## What has and has not been run against real hardware

Everything in this client is exercised by the test suite, and the simulator
path has been run end to end. The hardware path has not.

Specifically, as of 2026-09-24:

* The Colab path, `pip install "qly-sdk[qiskit]"` in a clean Python 3.11 and
  3.12 virtualenv followed by `run()` on the two free simulators, has been run
  against qly.app with the published 0.2.0. It was not run inside an actual
  Google Colab runtime, which preinstalls its own numpy and scipy.

* `submit`, `get_job`, `wait`, `run`, `devices`, `balance`, `estimate` and
  `calibration` are covered by tests against a stub that implements the
  `/api/v1` contract. That proves the client speaks the protocol correctly. It
  does not prove the protocol's other end behaves as expected on a real device.
* `examples/bell_pair.py` has been run end to end, both legs, against that same
  stub. Its hardware leg has never been submitted to a QPU.
* The BYOK path — running on your own IBM Quantum account rather than Qly's
  credit — has no production usage at all. Zero credentials have been stored
  through it, so treat it as unexercised rather than working.

None of this is a known defect; it is an absence of evidence, recorded here
because a green test suite can otherwise read as a claim it is not making. If
you are the first to run a circuit on real hardware through this client and it
misbehaves, that is worth reporting rather than assuming you held it wrong.

## Configuration

| Argument | Env var | Default |
|----------|---------|---------|
| `api_key` | `QLY_API_KEY` | — (required) |
| `base_url` | `QLY_BASE_URL` | `https://qly.app` |

## License

MIT
