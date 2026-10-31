"""Run one canary scenario against the live Docker Compose stack.

Works on Windows (PowerShell) and Unix. Requires Docker. Plots require
matplotlib (see requirements-dev.txt).

Each run, in order (CLAUDE.md §5.6):

1. Known state: stop any controller/load generator still running (a live
   controller keeps rewriting status.json with its old state), set the proxy
   to 100% stable, and move aside any stray live result files.
2. Deploy the scenario's canary: env vars + a recreated container.
3. Start the load generator, *then* the controller, so the first ramp stage
   bakes under real traffic instead of an idle stack.
4. Wait for the controller to promote or roll back.
5. Stop both, archive the run to results/archive/<scenario>_<stamp>/, plot
   it, and print a one-line summary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
ARCHIVE = RESULTS / "archive"
PROXY_CONF = ROOT / "proxy-conf" / "default.conf"
BOOTSTRAP_CONF = ROOT / "proxy" / "bootstrap.conf"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
COMPOSE_WITH_LOAD = [*COMPOSE, "--profile", "load"]
# Files the controller and load generator write into results/ while a run is live.
LIVE_FILES = ("decision_log.jsonl", "status.json", "loadgen.jsonl")

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


def setting(name: str, default: str) -> str:
    """A setting as docker compose resolves it: process env, then .env, then the default.

    Compose reads .env by itself; reading it here too keeps the ports the runner
    polls in step with the ports the stack actually publishes.
    """
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip():
                return value.strip()
    return default


def host_url(port_var: str, default_port: str, path: str) -> str:
    return f"http://127.0.0.1:{int(setting(port_var, default_port))}{path}"


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


def wait_proxy_weights(want: str, timeout: float = 20.0) -> None:
    """Block until nginx is serving with the given "stable/canary" weights.

    Reads the X-Perch-Weights header every rendered config stamps on its
    responses, so this sees what nginx has loaded, not just what is on disk.
    """
    url = host_url("PROXY_PORT", "8089", "/health")
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                seen = response.headers.get("X-Perch-Weights")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            seen = repr(exc)
        if seen == want:
            return
        time.sleep(0.5)
    raise SystemExit(f"proxy never loaded weights {want}; last saw {seen}")


def reset_proxy() -> None:
    """Send all traffic to stable before the canary is touched.

    Otherwise the load generator would start on whatever weights the previous
    run left behind: 100% canary after a promotion.
    """
    shutil.copyfile(BOOTSTRAP_CONF, PROXY_CONF)
    wait_proxy_weights("100/0")


def stop_rollout() -> None:
    """Stop the load generator and the controller.

    Stopping the controller is what makes back-to-back runs safe: a running
    one rewrites status.json and the decision log with its own state every
    poll, which a following run would otherwise read as its own result.
    """
    run([*COMPOSE_WITH_LOAD, "stop", "loadgen", "controller"], check=False)


def stash_stray_results(stamp: str) -> None:
    """Move live result files that predate this run out of the way.

    Every scripted run archives its own files when it ends, so anything still
    here came from outside the runner (e.g. `make up` starts a controller).
    Kept, but under unscripted_* which is gitignored.
    """
    present = [RESULTS / name for name in LIVE_FILES if (RESULTS / name).exists()]
    if not present:
        return
    dest = ARCHIVE / f"unscripted_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for src in present:
        shutil.move(str(src), str(dest / src.name))
    print(f"moved stray results to {dest}", flush=True)


def archive_run(name: str, stamp: str) -> Path:
    """Move this run's live files into results/archive/<scenario>_<stamp>/."""
    dest = ARCHIVE / f"{name}_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for filename in LIVE_FILES:
        src = RESULTS / filename
        if src.exists():
            shutil.move(str(src), str(dest / filename))
    return dest


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


def wait_terminal(expect: str, timeout: float) -> tuple[bool, dict]:
    """Wait for the rollout to reach a terminal state (promoted or rolled back).

    Returns as soon as either terminal state appears; promote and rollback
    are both final, so waiting on past the wrong one would only burn the
    timeout.

    Returns:
        (whether the expected state was reached, the last status read).
    """
    deadline = time.time() + timeout
    status: dict = {}
    while time.time() < deadline:
        status = read_status()
        if status.get("promoted") or status.get("rolled_back"):
            key = "promoted" if expect == "promote" else "rolled_back"
            return bool(status.get(key)), status
        time.sleep(2)
    print(f"timed out after {timeout}s waiting for {expect}; last status={status}", flush=True)
    return False, status


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def blast_radius(run_dir: Path) -> tuple[int, int]:
    """(requests the canary served, total requests) from the load generator's log."""
    rows = read_jsonl(run_dir / "loadgen.jsonl")
    canary = sum(1 for row in rows if row.get("service") == "canary")
    return canary, len(rows)


def time_to_terminal(decisions: list[dict]) -> float | None:
    """Seconds from the controller's first decision to its rollback/promote."""
    for row in decisions:
        if row.get("action") in ("rollback", "promote"):
            return round(row["unix"] - decisions[0]["unix"], 1)
    return None


def plot(run_dir: Path, title: str) -> None:
    """Plot the run into its archive folder and copy it to results/rollout.png (latest run)."""
    script = ROOT / "analysis" / "plot_results.py"
    cmd = [
        sys.executable,
        str(script),
        "--log",
        str(run_dir / "decision_log.jsonl"),
        "--out-dir",
        str(run_dir),
        "--title",
        title,
    ]
    try:
        run(cmd)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"plot skipped: {exc}", flush=True)
        return
    shutil.copyfile(run_dir / "rollout.png", RESULTS / "rollout.png")


def scenario_run(name: str, extra_env: dict | None = None) -> dict:
    """Run one scenario end to end and archive it. See the module docstring for the steps.

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
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    print(f"=== scenario {name} expect={spec['expect']} ===", flush=True)

    # 1. Known state. Build everything up front so no image build lands between
    # starting the load generator and starting the controller.
    stop_rollout()
    stash_stray_results(stamp)
    run([*COMPOSE_WITH_LOAD, "build"], env=env)
    run([*COMPOSE, "up", "-d", "stable", "canary", "proxy", "prometheus"], env=env)
    wait_http_ok(host_url("STABLE_PORT", "8091", "/health"))
    wait_http_ok(host_url("CANARY_PORT", "8092", "/health"))
    reset_proxy()

    # 2. Deploy the scenario's canary. A fresh process also restarts the
    # slow-onset fault clock.
    run([*COMPOSE, "up", "-d", "--force-recreate", "canary"], env=env)
    wait_http_ok(host_url("CANARY_PORT", "8092", "/health"))

    # 3-5. Traffic first, then the controller. Its first config write also makes
    # nginx reload and re-resolve the recreated canary's address.
    reached, status = False, {}
    try:
        run([*COMPOSE_WITH_LOAD, "up", "-d", "--force-recreate", "loadgen"], env=env)
        run([*COMPOSE, "up", "-d", "--force-recreate", "controller"], env=env)
        reached, status = wait_terminal(spec["expect"], spec["timeout"])
    finally:
        stop_rollout()
        run_dir = archive_run(name, stamp)

    decisions = read_jsonl(run_dir / "decision_log.jsonl")
    canary_n, total_n = blast_radius(run_dir)
    summary = {
        "scenario": name,
        "expect": spec["expect"],
        "ok": reached,
        "decision_mode": decisions[0]["evidence"].get("mode") if decisions else None,
        "final_weight": status.get("canary_weight"),
        "promoted": status.get("promoted"),
        "rolled_back": status.get("rolled_back"),
        "seconds": time_to_terminal(decisions),
        "canary_requests": canary_n,
        "total_requests": total_n,
        "blast_radius_pct": round(100.0 * canary_n / total_n, 2) if total_n else 0.0,
        "reason": status.get("reason"),
        "run_dir": run_dir.relative_to(ROOT).as_posix(),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    plot(run_dir, name)
    print("SUMMARY " + json.dumps(summary), flush=True)
    if not reached:
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
