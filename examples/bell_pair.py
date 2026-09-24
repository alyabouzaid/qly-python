#!/usr/bin/env python3
"""Run a Bell pair on a simulator, then on real hardware through BYOK.

    pip install qly-sdk
    export QLY_API_KEY=qly_live_...        # from https://qly.app/settings/api-keys
    python bell_pair.py                    # simulator only
    python bell_pair.py --hardware --device ibm_marrakesh   # also submit to a real QPU

The simulator leg is free and runs unattended. The hardware leg is opt-in
because it either spends credit or runs against your own IBM Quantum account,
and neither should happen because someone ran an example to see what it did.

BYOK ("bring your own key") means Qly submits to IBM using credentials you
stored at https://qly.app/settings/providers, encrypted under KMS. IBM bills
your account; Qly charges nothing. Without BYOK the same circuit runs on Qly's
prepaid credit instead, and this script tells you which one is about to happen.

Status, as of 2026-09-19: the simulator leg has been run end to end. The
--hardware leg has only ever been run against a stub implementing the /api/v1
contract, never against a QPU, and no credential has yet been stored through
the BYOK path in production. If you are the first to run it for real and it
misbehaves, that is worth reporting rather than assuming you held it wrong.
"""

from __future__ import annotations

import argparse
import sys

from qly import (
    AuthenticationError,
    CircuitError,
    InsufficientBalanceError,
    Qly,
    QlyError,
)

# A Bell pair: H on qubit 0, CNOT onto qubit 1, measure both. The result should
# be ~50% "00" and ~50% "11", and close to 0% for "01" and "10" — those two are
# the states entanglement forbids, so on hardware their weight is a direct read
# on how noisy the device was.
BELL = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0],q[1];
measure q -> c;
"""


def show(job) -> None:
    """Print the histogram, then whatever the device reported about the run."""
    counts = job.counts or {}
    total = sum(counts.values()) or 1
    print(f"  status {job.status}")
    for state in sorted(counts, key=lambda s: -counts[s]):
        n = counts[state]
        bar = "#" * round(40 * n / total)
        print(f"  {state}  {n:>6}  {100 * n / total:5.1f}%  {bar}")

    correlated = counts.get("00", 0) + counts.get("11", 0)
    print(f"  correlated (00 or 11): {100 * correlated / total:.1f}%")

    f = job.run_facts
    if f.execution_ms is not None:
        print(f"  on the device:  {f.execution_ms:.1f} ms")
    if f.qpu_seconds is not None:
        print(f"  qpu seconds:    {f.qpu_seconds:.2f}")
    if f.physical_qubits:
        print(f"  physical qubits: {f.physical_qubits}")
    if f.native_gate_counts:
        gates = sorted(f.native_gate_counts.items(), key=lambda kv: -kv[1])
        print("  compiled to:    " + "  ".join(f"{g}×{n}" for g, n in gates))
        print(f"  two-qubit gates: {f.two_qubit_gates}")


def pick_simulator(client: Qly):
    """A free simulator, preferring one that samples.

    clifft-sim draws random shots, so counts vary from run to run like a real
    device. ionq/simulator returns the ideal probabilities scaled to the shot
    count, so it gives the same histogram every time, which is the wrong thing
    to compare a noisy hardware run against.
    """
    sims = [d for d in client.devices() if d.is_simulator and not d.raw.get("access_required")]
    if not sims:
        sys.exit("No simulator available to this account.")
    free = [d for d in sims if not d.price or "free" in str(d.price).lower()]
    for d in free:
        if d.provider == "clifft":
            return d
    return (free or sims)[0]


def pick_qpu(client: Qly, device_id):
    """The IBM device the caller named, or a list of the choices.

    The machine is deliberately not chosen here. Queues and each machine's
    readings change from day to day, so the caller picks from client.devices()
    or qly.app/router on the day, and this script does not guess for them.
    """
    qpus = [
        d
        for d in client.devices()
        if not d.is_simulator and d.provider == "ibm" and not d.raw.get("access_required")
    ]
    if device_id is None:
        ids = ", ".join(d.id for d in qpus) or "none available to this account"
        sys.exit(f"--hardware needs --device. IBM devices: {ids}. See also https://qly.app/router")
    for d in qpus:
        if d.id == device_id:
            return d
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", action="store_true", help="also run on a real QPU (needs --device)")
    parser.add_argument("--device", help="IBM device id for --hardware, e.g. one listed by client.devices()")
    parser.add_argument("--shots", type=int, default=1024)
    args = parser.parse_args()
    # One guard around everything that talks to the API. A bad key fails on the
    # first call, not at construction, so catching only the constructor left the
    # most likely first-run failure printing a traceback.
    try:
        return run(args)
    except QlyError as exc:
        return fail(exc)


def run(args: argparse.Namespace) -> int:
    client = Qly()  # reads QLY_API_KEY

    print(f"balance: {client.balance().formatted}\n")

    # -- simulator ---------------------------------------------------------
    sim = pick_simulator(client)
    print(f"simulator: {sim.name} ({sim.provider})")
    job = client.run(BELL, provider=sim.provider, device=sim.id, shots=args.shots)
    show(job)

    if not args.hardware:
        print("\nRe-run with --hardware to submit the same circuit to a real QPU.")
        return 0

    # -- hardware ----------------------------------------------------------
    qpu = pick_qpu(client, args.device)
    if qpu is None:
        print(f"\nNo IBM QPU called {args.device!r} is available to this account.")
        print("List them with client.devices(), or connect your IBM Quantum account at https://qly.app/settings/providers.")
        return 1

    print(f"\nQPU: {qpu.name} ({qpu.qubits} qubits, status {qpu.status})")

    # Calibration before submission: a device whose two-qubit error rate moved
    # overnight will turn this Bell pair into noise, and qubit count alone will
    # not tell you that.
    cal = client.calibration(qpu)
    if cal.is_live:
        for m in cal.metrics[:5]:
            print(f"  {m.label}: {m.value}{(' ' + m.unit) if m.unit else ''}")
    elif cal.note:
        print(f"  {cal.note}")

    # Price it before spending anything. On BYOK this reports billing="byok"
    # and no charge, because IBM bills you directly.
    est = client.estimate(BELL, provider=qpu.provider, device=qpu.id, shots=args.shots)
    if est.billing == "byok":
        print("  billing: your own IBM Quantum account (Qly charges nothing)")
    elif est.exact and est.cost_formatted:
        print(f"  cost: {est.cost_formatted} from your Qly credit")
    elif est.cost_range_cents:
        lo = est.cost_range_cents.get("min", 0) / 100
        hi = est.cost_range_cents.get("max", 0) / 100
        print(f"  cost: ${lo:.2f}-${hi:.2f} (exact charge known after the run)")
    if est.sufficient_balance is False:
        print("  not enough credit — https://qly.app/pricing")
        return 1
    for w in est.warnings:
        print(f"  warning: {w}")

    if input("\nSubmit to hardware? [y/N] ").strip().lower() != "y":
        print("Nothing submitted.")
        return 0

    print("\nQueued. This can take minutes to hours depending on the queue.")
    job = client.run(BELL, provider=qpu.provider, device=qpu.id, shots=args.shots, timeout=7200)
    show(job)
    return 0


def fail(exc: QlyError) -> int:
    """Print the error the way the exception type says it should be acted on."""
    if isinstance(exc, AuthenticationError):
        print(f"\n{exc}", file=sys.stderr)
        # The server's own 401 already points at the key page; only add the
        # pointer when whatever raised this did not.
        if "settings/api-keys" not in str(exc):
            print("Create a key at https://qly.app/settings/api-keys.", file=sys.stderr)
    elif isinstance(exc, InsufficientBalanceError):
        print(f"\nOut of credit: {exc}", file=sys.stderr)
    elif isinstance(exc, CircuitError):
        # The provider named what it rejected; retrying unchanged will not help.
        print(f"\nThe circuit was rejected: {exc}", file=sys.stderr)
    else:
        print(f"\n{type(exc).__name__}: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
