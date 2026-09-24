# Changelog

## 0.3.0 (prepared, not released)

Everything here is already served by qly.app; 0.2.0 on PyPI simply predates
the client side of it. None of it is needed to run a circuit: the Colab path
(`pip install "qly-sdk[qiskit]"`, then `run()`) works on 0.2.0.

### Added

- `Qly.estimate()` prices a run before it is submitted (`POST /api/v1/estimate`).
  Check `.exact` before treating `.cost_cents` as a price: per-QPU-second
  devices can only give a range, and say why in `.note`.
- `Qly.calibration()` reads a device's live calibration
  (`GET /api/v1/calibration`). `Calibration.is_live` separates "the provider
  publishes nothing" from "the numbers are clean".
- `Job.run_facts`: what the device reported about the run, including the
  compiled circuit, the physical qubits it landed on, time on the device and
  IonQ's debiased distribution. A fact the provider did not report is `None`,
  which is not zero. `qpu_seconds` is only ever what the device reported;
  `billed_qpu_seconds` is the charge either way.
- `CircuitError` for a 400 or 422, meaning the request itself was rejected
  (unparseable QASM, an instruction the device does not run, shots out of
  range). It subclasses `APIError`, so existing handlers keep working. The
  server's wording is passed through unchanged because it names the
  offending statement.
- `qly estimate` and `qly calibration` on the command line, and run facts in
  `qly job` output.
- `examples/bell_pair.py`.

### Changed

- `InsufficientBalanceError` messages now say what to do next.
- The README says which paths have never been run against real hardware.

### Not in this release

- `initial_layout` on `run()` / `submit()`. It waits on `/api/v1` accepting the
  field; nothing on the server takes it yet.
