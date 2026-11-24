from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONDITIONS = "rtt20:20:0,rtt100:100:0,rtt200:200:0"
NETNS_PLAYER = {"1": "clientA", "2": "clientB"}


@dataclass(frozen=True)
class Condition:
    label: str
    rtt_ms: float
    loss: float


def parse_conditions(spec: str) -> list[Condition]:
    conditions: list[Condition] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(":")
        label = parts[0]
        rtt_ms = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
        loss = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
        conditions.append(Condition(label, rtt_ms, loss))
    if not conditions:
        raise ValueError("no conditions given")
    return conditions


def parse_modes(spec: str) -> list[str]:
    modes = [part.strip() for part in spec.split(",") if part.strip()]
    valid = {"c0", "c1", "c2", "c3"}
    unknown = [mode for mode in modes if mode not in valid]
    if unknown:
        raise ValueError(f"unknown modes: {', '.join(unknown)}")
    return modes


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def netem_command(action: str, condition: Condition, args) -> list[str]:
    script = os.path.join(REPO_ROOT, "topology", "netem.sh")
    command = ["sudo", "-n", "bash", script, action]
    if action == "apply":
        command += [
            "--rtt-ms",
            str(condition.rtt_ms),
            "--loss",
            str(condition.loss),
            "--jitter-ms",
            str(args.jitter_ms),
            "--loss-correlation",
            str(args.loss_correlation),
        ]
    return command


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def wait_for_server(proc: subprocess.Popen, log_path: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early; see {log_path}")
        try:
            with open(log_path, encoding="utf-8", errors="replace") as handle:
                if "server listening on" in handle.read():
                    return
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"server did not start within {timeout}s; see {log_path}")


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_once(condition: Condition, mode: str, run_index: int, args, out_dir: str) -> None:
    run_dir = os.path.join(out_dir, condition.label, mode, f"run{run_index}")
    os.makedirs(run_dir, exist_ok=True)

    if args.netns:
        port = args.port or 9000
    else:
        port = free_udp_port()
    server_host = "10.0.3.10" if args.netns else "127.0.0.1"

    server_log_path = os.path.join(run_dir, "server.log")
    server_log = open(server_log_path, "w", encoding="utf-8")
    server_prefix = ["ip", "netns", "exec", "server"] if args.netns else []
    server_cmd = server_prefix + [
        sys.executable,
        "-m",
        "netcode_lab.server",
        "--host",
        server_host,
        "--port",
        str(port),
        "--tick-rate",
        str(args.tick_rate),
        "--snapshot-rate",
        str(args.snapshot_rate),
        "--log-level",
        "INFO",
    ]
    server = None
    clients: list[subprocess.Popen] = []
    client_logs = []
    try:
        server = subprocess.Popen(
            server_cmd,
            cwd=REPO_ROOT,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        wait_for_server(server, server_log_path, args.startup_timeout)

        for player in range(1, args.players + 1):
            csv_path = os.path.join(run_dir, f"player{player}.csv")
            client_log_path = os.path.join(run_dir, f"player{player}.log")
            log_handle = open(client_log_path, "w", encoding="utf-8")
            client_logs.append(log_handle)
            prefix = (
                ["ip", "netns", "exec", NETNS_PLAYER[str(player)]] if args.netns else []
            )
            client_cmd = prefix + [
                sys.executable,
                "-m",
                "netcode_lab.client",
                "--host",
                server_host,
                "--port",
                str(port),
                "--mode",
                mode,
                "--tick-rate",
                str(args.tick_rate),
                "--player",
                str(player),
                "--duration",
                str(args.duration),
                "--print-interval",
                str(args.print_interval),
                "--patrol-period",
                str(args.patrol_period),
                "--interpolation-delay-ms",
                str(args.interpolation_delay_ms),
                "--redundancy",
                str(args.redundancy),
                "--csv",
                csv_path,
            ]
            clients.append(
                subprocess.Popen(
                    client_cmd,
                    cwd=REPO_ROOT,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
            )

        for proc in clients:
            proc.wait(timeout=args.duration + args.timeout_slack)
            if proc.returncode != 0:
                raise RuntimeError(f"client exited with code {proc.returncode}")
    finally:
        for proc in clients:
            stop_process(proc)
        stop_process(server)
        server_log.close()
        for handle in client_logs:
            handle.close()

    meta = {
        "condition": condition.label,
        "rtt_ms": condition.rtt_ms,
        "loss_percent": condition.loss,
        "jitter_ms": args.jitter_ms,
        "loss_correlation": args.loss_correlation,
        "netem_applied": bool(args.apply_netem),
        "netns": bool(args.netns),
        "mode": mode,
        "run": run_index,
        "players": args.players,
        "duration_s": args.duration,
        "tick_rate": args.tick_rate,
        "snapshot_rate": args.snapshot_rate,
        "redundancy": args.redundancy,
        "interpolation_delay_ms": args.interpolation_delay_ms,
        "port": port,
        "server_host": server_host,
        "git_commit": git_commit(),
        "python": sys.version.split()[0],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="experiments.run")
    parser.add_argument(
        "--conditions",
        default=DEFAULT_CONDITIONS,
        help="comma list label:rtt_ms:loss (e.g. rtt20:20:0,rtt100:100:0)",
    )
    parser.add_argument("--modes", default="c0,c1,c2,c3")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--players", type=int, default=2, choices=(1, 2))
    parser.add_argument("--tick-rate", type=float, default=30.0)
    parser.add_argument("--snapshot-rate", type=float, default=20.0)
    parser.add_argument("--redundancy", type=int, default=3)
    parser.add_argument("--interpolation-delay-ms", type=float, default=100.0)
    parser.add_argument("--print-interval", type=float, default=0.25)
    parser.add_argument("--patrol-period", type=float, default=0.75)
    parser.add_argument("--jitter-ms", type=float, default=0.0)
    parser.add_argument("--loss-correlation", type=float, default=0.0)
    parser.add_argument("--outdir", default="results")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--startup-timeout", type=float, default=5.0)
    parser.add_argument("--timeout-slack", type=float, default=15.0)
    parser.add_argument(
        "--apply-netem",
        action="store_true",
        help="apply the condition with topology/netem.sh (Linux, needs sudo -n)",
    )
    parser.add_argument(
        "--netns",
        action="store_true",
        help="run server/client inside the topology namespaces (needs root)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    conditions = parse_conditions(args.conditions)
    modes = parse_modes(args.modes)

    if args.apply_netem and sys.platform != "linux":
        raise SystemExit("--apply-netem requires Linux")
    if args.apply_netem and not args.netns:
        print("warning: --apply-netem without --netns affects only localhost traffic")

    out_dir = os.path.join(
        REPO_ROOT, args.outdir, time.strftime("exp_%Y%m%d_%H%M%S")
    )
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "experiment.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {
                "conditions": [asdict(condition) for condition in conditions],
                "modes": modes,
                "args": {k: v for k, v in vars(args).items()},
                "git_commit": git_commit(),
            },
            handle,
            indent=2,
            sort_keys=True,
        )

    print(f"results directory: {out_dir}")
    total = len(conditions) * len(modes) * args.runs
    done = 0
    netem_on = False
    try:
        for condition in conditions:
            if args.apply_netem:
                subprocess.run(
                    netem_command("apply", condition, args),
                    cwd=REPO_ROOT,
                    check=True,
                )
                netem_on = True
            for mode in modes:
                for run_index in range(1, args.runs + 1):
                    done += 1
                    print(
                        f"[{done}/{total}] condition={condition.label} "
                        f"mode={mode} run={run_index}"
                    )
                    run_once(condition, mode, run_index, args, out_dir)
            if netem_on:
                subprocess.run(
                    netem_command("clear", condition, args),
                    cwd=REPO_ROOT,
                    check=False,
                )
                netem_on = False
    finally:
        if netem_on:
            subprocess.run(
                netem_command("clear", conditions[-1], args),
                cwd=REPO_ROOT,
                check=False,
            )

    print(f"done. aggregate with: python -m experiments.aggregate --results-dir {out_dir}")


if __name__ == "__main__":
    main()
