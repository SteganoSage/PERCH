"""Run one canary scenario against the live Docker Compose stack.

Works on Windows (PowerShell) and Unix. Requires Docker. Plots require
matplotlib (see requirements-dev.txt).

Scenario variables are passed via subprocess environment variables to Docker
Compose. The nginx proxy automatically reloads when the controller adjusts
traffic weights.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]

SCENARIOS = {
    "good": {
        "CANARY_ERROR_RATE": "0.0",
        "CANARY_LATENCY_MS_MEAN": "40",
        "CANARY_LATENCY_MS_JITTER": "10",
        "CANARY_DEGRADE_ENDPOINT": "",
        "CANARY_DEGRADE_RAMP_SECONDS": "",
        "expect": "promote",
        "timeout": 300,
    },
    "bad-error": {
        "CANARY_ERROR_RATE": "0.30",
        "CANARY_LATENCY_MS_MEAN": "40",
        "CANARY_LATENCY_MS_JITTER": "10",
        "CANARY_DEGRADE_ENDPOINT": "",
        "CANARY_DEGRADE_RAMP_SECONDS": "",
        "expect": "rollback",
        "timeout": 180,
    },
    "bad-latency": {
        "CANARY_ERROR_RATE": "0.0",
        "CANARY_LATENCY_MS_MEAN": "400",
        "CANARY_LATENCY_MS_JITTER": "120",
        "CANARY_DEGRADE_ENDPOINT": "",
        "CANARY_DEGRADE_RAMP_SECONDS": "",
        "expect": "rollback",
        "timeout": 180,
    },
    "bad-slow-onset": {
        "CANARY_ERROR_RATE": "0.30",
        "CANARY_LATENCY_MS_MEAN": "40",
        "CANARY_LATENCY_MS_JITTER": "10",
        "CANARY_DEGRADE_ENDPOINT": "",
        "CANARY_DEGRADE_RAMP_SECONDS": "120",
        "expect": "rollback",
        "timeout": 240,
    },
    "bad-partial": {
        "CANARY_ERROR_RATE": "0.60",
        "CANARY_LATENCY_MS_MEAN": "40",
        "CANARY_LATENCY_MS_JITTER": "10",
        "CANARY_DEGRADE_ENDPOINT": "/work/checkout",
        "CANARY_DEGRADE_RAMP_SECONDS": "",
        "expect": "rollback",
        "timeout": 180,
    },
}


def run(cmd: list[str], env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command in the project root directory.

    Args:
        cmd: The command and its arguments as a list of strings.
        env: Optional environment variables to merge with the current process environment.
        check: If True, raises a subprocess.CalledProcessError if the command fails.

    Returns:
        The result of the subprocess run.
    """
    merged = os.environ.copy()
    if env:
        merged.update(env)
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, env=merged, check=check)


def rotate_logs(name: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RESULTS / "archive" / f"{name}_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for filename in ("decision_log.jsonl", "status.json", "loadgen.jsonl"):
        src = RESULTS / filename
        if src.exists():
            shutil.move(str(src), str(dest / filename))
    (RESULTS / "loadgen.jsonl").write_text("", encoding="utf-8")
    return dest


def wait_http_ok(url: str, timeout: float = 60.0) -> None:
    """Wait for an HTTP endpoint to return a 2xx success code.

    Polls the endpoint once per second until it returns a successful response
    or the timeout is reached.

    Args:
        url: The HTTP URL to poll.
        timeout: Maximum time to wait in seconds.

    Raises:
        SystemExit: If the endpoint doesn't respond successfully within the timeout.
    """
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(1)
    raise SystemExit(f"timed out waiting for {url}: {last}")


def read_status() -> dict:
    """Read the current status from results/status.json.

    Returns:
        A dictionary containing the parsed JSON status, or an empty dictionary
        if the file doesn't exist or is invalid JSON.
    """
    path = RESULTS / "status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def wait_terminal(expect: str, timeout: float) -> dict:
    """Wait for the rollout to reach a terminal state (promoted or rolled_back).

    Polls the status.json file every 2 seconds until the expected state is reached.

    Args:
        expect: The expected terminal state ("promote" or "rollback").
        timeout: Maximum time to wait in seconds.

    Returns:
        The final status dictionary.

    Raises:
        SystemExit: If the terminal state is not reached within the timeout.
    """
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = read_status()
        if expect == "promote" and last.get("promoted"):
            return last
        if expect == "rollback" and last.get("rolled_back"):
            return last
        time.sleep(2)
    raise SystemExit(
        f"timed out after {timeout}s waiting for {expect}; last status={last}"
    )


def blast_radius() -> tuple[int, int]:
    """Calculate the blast radius of the canary deployment.

    Reads the load generator logs to count how many total requests were made
    and how many hit the canary service.

    Returns:
        A tuple of (canary_requests, total_requests).
    """
    path = RESULTS / "loadgen.jsonl"
    canary = total = 0
    if not path.exists():
        return 0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("service") == "canary":
            canary += 1
    return canary, total


def plot(name: str) -> None:
    """Generate a rollout plot for the scenario.

    Invokes the plot_results.py analysis script to create results/rollout.png.

    Args:
        name: Name of the scenario (used as the plot title).
    """
    script = ROOT / "analysis" / "plot_results.py"
    cmd = [sys.executable, str(script), "--log", str(RESULTS / "decision_log.jsonl"), "--out-dir", str(RESULTS), "--title", name]
    try:
        run(cmd)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"plot skipped: {exc}", flush=True)


def scenario_run(name: str, extra_env: dict | None = None) -> dict:
    """Run a full end-to-end scenario test.

    Sets up the environment, brings up the Docker Compose stack, waits for
    the controller to reach a terminal decision, tears down the load generator,
    calculates metrics, generates a plot, and outputs a summary.

    Args:
        name: The name of the scenario to run.
        extra_env: Additional environment variables to apply.

    Returns:
        A dictionary summarizing the scenario results.
    """
    spec = SCENARIOS[name]
    env = {k: v for k, v in spec.items() if k not in ("expect", "timeout")}
    if extra_env:
        env.update(extra_env)

    print(f"=== scenario {name} expect={spec['expect']} ===", flush=True)
    rotate_logs(name)

    # Pass scenario vars via subprocess env to Docker Compose
    started = time.time()
    try:
        run([*COMPOSE, "up", "-d", "--build", "stable", "canary", "proxy", "prometheus"], env=env)
        stable_port = int(env.get("STABLE_PORT", os.environ.get("STABLE_PORT", "8091")))
        canary_port = int(env.get("CANARY_PORT", os.environ.get("CANARY_PORT", "8092")))
        wait_http_ok(f"http://127.0.0.1:{stable_port}/health")
        wait_http_ok(f"http://127.0.0.1:{canary_port}/health")

        run([*COMPOSE, "up", "-d", "--build", "--force-recreate", "canary"], env=env)
        wait_http_ok(f"http://127.0.0.1:{canary_port}/health")

        run([*COMPOSE, "up", "-d", "--build", "--force-recreate", "controller"], env=env)

        run([*COMPOSE, "--profile", "load", "up", "-d", "--build", "--force-recreate", "loadgen"], env=env)

        status = wait_terminal(spec["expect"], spec["timeout"])
        elapsed = time.time() - started
    finally:
        pass

    run([*COMPOSE, "stop", "loadgen"], check=False)
    canary_n, total_n = blast_radius()
    plot(name)

    summary = {
        "scenario": name,
        "expect": spec["expect"],
        "final_weight": status.get("canary_weight"),
        "promoted": status.get("promoted"),
        "rolled_back": status.get("rolled_back"),
        "seconds": round(elapsed, 1),
        "canary_requests": canary_n,
        "total_requests": total_n,
        "blast_radius_pct": round(100.0 * canary_n / total_n, 2) if total_n else 0.0,
        "reason": status.get("reason"),
    }
    print("SUMMARY " + json.dumps(summary), flush=True)
    ok = (spec["expect"] == "promote" and status.get("promoted")) or (
        spec["expect"] == "rollback" and status.get("rolled_back")
    )
    if not ok:
        raise SystemExit(f"scenario {name} did not reach expected terminal state")
    return summary


def main() -> None:
    """Main entrypoint for the scenario runner.

    Parses command-line arguments to execute either a specific scenario
    or all scenarios sequentially.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=[*SCENARIOS, "all"])
    args = parser.parse_args()
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        scenario_run(name)


if __name__ == "__main__":
    main()
