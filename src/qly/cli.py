"""The ``qly`` command-line tool.

A thin wrapper over :class:`qly.Qly` so you can submit circuits and check jobs
from a shell without writing Python::

    qly configure                      # store your API key
    qly devices
    qly submit bell.qasm --provider ionq --device simulator --wait
    qly job <job-id>
    qly jobs --limit 10
    qly balance

The API key is resolved in this order: ``--api-key``, the ``QLY_API_KEY``
environment variable, then the config file written by ``qly configure``
(``~/.config/qly/config.json``).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .client import DEFAULT_BASE_URL, Qly
from .exceptions import AuthenticationError, QlyError
from .models import Device, Job
from .version import __version__

CONFIG_ENV = "QLY_CONFIG"


def _config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "qly" / "config.json"


def _load_config() -> Dict[str, Any]:
    path = _config_path()
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_config(config: Dict[str, Any]) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    # The file holds a live credential; keep it owner-only.
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def _make_client(args: argparse.Namespace) -> Qly:
    config = _load_config()
    api_key: Optional[str] = (
        getattr(args, "api_key", None) or os.environ.get("QLY_API_KEY") or config.get("api_key")
    )
    base_url: Optional[str] = (
        getattr(args, "base_url", None) or os.environ.get("QLY_BASE_URL") or config.get("base_url")
    )
    if not api_key:
        raise AuthenticationError(
            "No API key found. Run `qly configure`, set QLY_API_KEY, or pass --api-key. "
            "Create a key at https://qly.app/settings/api-keys."
        )
    return Qly(api_key=api_key, base_url=base_url)


# -- Rendering ---------------------------------------------------------------


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _table(rows: List[List[str]], headers: List[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines.extend(fmt.format(*row) for row in rows)
    return "\n".join(lines)


def _device_rows(devices: List[Device]) -> List[List[str]]:
    return [
        [d.provider, d.id, d.name, str(d.qubits), d.type, d.status, d.price or ""]
        for d in devices
    ]


def _job_summary(job: Job) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "id": job.id,
        "provider": job.provider,
        "device": job.device,
        "status": job.status,
        "shots": job.shots,
    }
    if job.counts is not None:
        summary["counts"] = job.counts
    if job.error:
        summary["error"] = job.error
    return summary


def _print_job(job: Job, as_json: bool) -> None:
    if as_json:
        _print_json(_job_summary(job))
        return
    print(f"job      {job.id}")
    if job.provider:
        print(f"provider {job.provider}" + (f" / {job.device}" if job.device else ""))
    print(f"status   {job.status}")
    if job.shots:
        print(f"shots    {job.shots}")
    counts = job.counts
    if counts is not None:
        print("counts:")
        total = sum(counts.values()) or 1
        for state in sorted(counts, key=lambda s: -counts[s]):
            n = counts[state]
            print(f"  {state}  {n:>6}  ({100 * n / total:.1f}%)")
    if job.error:
        print(f"error    {job.error}")


# -- Commands ----------------------------------------------------------------


def _cmd_configure(args: argparse.Namespace) -> int:
    api_key = args.api_key
    if not api_key:
        api_key = getpass.getpass("Qly API key (qly_live_...): ").strip()
    if not api_key:
        print("No key entered, nothing saved.", file=sys.stderr)
        return 1
    if not api_key.startswith("qly_"):
        print(
            "Warning: that does not look like a Qly key (expected a qly_live_... value).",
            file=sys.stderr,
        )
    config = _load_config()
    config["api_key"] = api_key
    if args.base_url:
        config["base_url"] = args.base_url
    path = _save_config(config)
    print(f"Saved to {path}")
    return 0


def _cmd_devices(args: argparse.Namespace) -> int:
    devices = _make_client(args).devices()
    if args.provider:
        devices = [d for d in devices if d.provider == args.provider]
    if args.json:
        _print_json([d.raw for d in devices])
        return 0
    if not devices:
        print("No devices available.")
        return 0
    headers = ["PROVIDER", "ID", "NAME", "QUBITS", "TYPE", "STATUS", "PRICE"]
    print(_table(_device_rows(devices), headers))
    return 0


def _cmd_balance(args: argparse.Namespace) -> int:
    balance = _make_client(args).balance()
    if args.json:
        _print_json({"balance_cents": balance.cents, "balance_usd": balance.usd})
    else:
        print(balance.formatted)
    return 0


def _read_qasm(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    with open(source) as f:
        return f.read()


def _cmd_submit(args: argparse.Namespace) -> int:
    qasm = _read_qasm(args.file)
    client = _make_client(args)
    job = client.submit(
        qasm,
        provider=args.provider,
        device=args.device,
        shots=args.shots,
        device_name=args.device_name,
    )
    if args.wait:
        job = client.wait(job, timeout=args.timeout, raise_on_failure=False)
    _print_job(job, args.json)
    return 1 if job.failed else 0


def _cmd_job(args: argparse.Namespace) -> int:
    client = _make_client(args)
    if args.wait:
        job = client.wait(args.job_id, timeout=args.timeout, raise_on_failure=False)
    else:
        job = client.get_job(args.job_id)
    _print_job(job, args.json)
    return 1 if job.failed else 0


def _cmd_jobs(args: argparse.Namespace) -> int:
    jobs = _make_client(args).jobs(limit=args.limit)
    if args.json:
        _print_json([j.raw for j in jobs])
        return 0
    if not jobs:
        print("No jobs yet.")
        return 0
    rows = [
        [
            j.id,
            j.provider or "",
            j.device or "",
            j.status,
            str(j.shots or ""),
            j.created_at or "",
        ]
        for j in jobs
    ]
    print(_table(rows, ["ID", "PROVIDER", "DEVICE", "STATUS", "SHOTS", "CREATED"]))
    return 0


# -- Entry point ---------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", help="API key (overrides QLY_API_KEY and the config file)")
    parser.add_argument("--base-url", help=f"API host (default {DEFAULT_BASE_URL})")
    parser.add_argument("--json", action="store_true", help="print raw JSON instead of a table")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qly",
        description="Submit quantum circuits to real hardware from the command line.",
    )
    parser.add_argument("--version", action="version", version=f"qly {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("configure", help="store your API key locally")
    p.add_argument("--api-key", help="key to store (prompted for securely if omitted)")
    p.add_argument("--base-url", help="non-default API host to store")
    p.set_defaults(func=_cmd_configure)

    p = sub.add_parser("devices", help="list available quantum devices")
    _add_common(p)
    p.add_argument("--provider", help="only show one provider (e.g. ibm, ionq, aws)")
    p.set_defaults(func=_cmd_devices)

    p = sub.add_parser("balance", help="show your prepaid balance")
    _add_common(p)
    p.set_defaults(func=_cmd_balance)

    p = sub.add_parser("submit", help="submit an OpenQASM 2.0 file as a job")
    _add_common(p)
    p.add_argument("file", help="path to a .qasm file, or - to read from stdin")
    p.add_argument("--provider", required=True, help="e.g. ibm, ionq, aws, azure, quantinuum")
    p.add_argument("--device", required=True, help="device id, e.g. ibm_kingston or simulator")
    p.add_argument("--shots", type=int, default=1024)
    p.add_argument("--device-name", help="display name stored with the job")
    p.add_argument("--wait", action="store_true", help="poll until the job finishes")
    p.add_argument("--timeout", type=float, default=600.0, help="max seconds to wait (with --wait)")
    p.set_defaults(func=_cmd_submit)

    p = sub.add_parser("job", help="show one job's status and results")
    _add_common(p)
    p.add_argument("job_id")
    p.add_argument("--wait", action="store_true", help="poll until the job finishes")
    p.add_argument("--timeout", type=float, default=600.0, help="max seconds to wait (with --wait)")
    p.set_defaults(func=_cmd_job)

    p = sub.add_parser("jobs", help="list your recent jobs")
    _add_common(p)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=_cmd_jobs)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except QlyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
