"""Open-loop load generator.

Offers a constant request rate at the proxy regardless of how slow responses
are. A closed-loop generator would back off exactly when the canary degrades
and hide the fault from the controller.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import time
from pathlib import Path

import httpx


def parse_mix(raw: str) -> list[tuple[str, float]]:
    """Parse a comma-separated path:weight string into a weighted path list.

    Example: 'work:2,work/checkout:1' -> [('work', 2.0), ('work/checkout', 1.0)]

    Args:
        raw: The raw weight mix string.

    Returns:
        A list of (path, weight) tuples.
    """
    parts = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            path, weight = item.rsplit(":", 1)
            parts.append((path.strip().lstrip("/"), float(weight)))
        else:
            parts.append((item.lstrip("/"), 1.0))
    if not parts:
        parts = [("work", 1.0)]
    return parts


def pick_path(mix: list[tuple[str, float]]) -> str:
    """Randomly pick a path based on its configured weight.

    Args:
        mix: A list of (path, weight) tuples.

    Returns:
        A randomly chosen path string (prefixed with '/').
    """
    paths, weights = zip(*mix)
    chosen = random.choices(list(paths), weights=list(weights), k=1)[0]
    return "/" + chosen.lstrip("/")


async def one_request(
    client: httpx.AsyncClient,
    target: str,
    mix: list[tuple[str, float]],
    sink: asyncio.Queue,
) -> None:
    """Execute a single HTTP GET request against the target service.

    Picks a random path, makes the request, records the latency and status code,
    and places the result object into the sink queue for logging.

    Args:
        client: The httpx AsyncClient.
        target: The base URL of the target service.
        mix: Path weight distribution.
        sink: Async queue where request metrics will be deposited.
    """
    path = pick_path(mix)
    url = target.rstrip("/") + path
    t0 = time.perf_counter()
    service = ""
    status = 0
    try:
        response = await client.get(url)
        status = response.status_code
        try:
            service = str(response.json().get("service", ""))
        except Exception:
            service = ""
    except Exception:
        status = 0
    sink.put_nowait(
        {
            "ts": time.time(),
            "path": path,
            "status": status,
            "latency_ms": (time.perf_counter() - t0) * 1000.0,
            "service": service,
        }
    )


async def writer(sink: asyncio.Queue, path: Path, stop: asyncio.Event) -> None:
    """Background task to write generated request metrics to a JSONL log file.

    Continuously drains the sink queue and appends JSON lines to the output path
    until the stop event is set and the queue is completely empty.

    Args:
        sink: Async queue supplying request metrics.
        path: File path to append JSON logs to.
        stop: Event signaling the load generator is shutting down.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        while True:
            try:
                item = await asyncio.wait_for(sink.get(), timeout=0.5)
            except TimeoutError:
                if stop.is_set() and sink.empty():
                    return
                continue
            fh.write(json.dumps(item) + "\n")
            sink.task_done()


async def run(args: argparse.Namespace) -> None:
    """Core load generator loop.

    Spawns concurrent requests at the specified rate (RPS) in an open-loop
    fashion. Ensures we don't back off even if the service is slow or failing.

    Args:
        args: Parsed command-line arguments.
    """
    mix = parse_mix(args.mix)
    stop = asyncio.Event()

    def _stop(*_):
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    sink: asyncio.Queue = asyncio.Queue()
    out = Path(args.output)
    writer_task = asyncio.create_task(writer(sink, out, stop))
    interval = 1.0 / args.rps
    deadline = None if args.duration <= 0 else (time.monotonic() + args.duration)
    next_t = time.monotonic()
    in_flight: set[asyncio.Task] = set()

    async with httpx.AsyncClient(timeout=10.0) as client:
        print(
            f"loadgen target={args.target} rps={args.rps} mix={args.mix}",
            flush=True,
        )
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            task = asyncio.create_task(one_request(client, args.target, mix, sink))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
            next_t += interval
            sleep_for = next_t - time.monotonic()
            if sleep_for > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=sleep_for)
                except TimeoutError:
                    pass
            # If we fell behind, skip sleep and keep offering — do not slow down.
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)

    stop.set()
    await writer_task


def main() -> None:
    """Main entrypoint for the load generator script.

    Parses command-line arguments and kicks off the asyncio event loop.
    """
    parser = argparse.ArgumentParser(description="Open-loop canary load generator")
    parser.add_argument(
        "--target",
        default=os.environ.get("LOADGEN_TARGET", "http://proxy:80"),
    )
    parser.add_argument("--rps", type=float, default=float(os.environ.get("LOADGEN_RPS", "50")))
    parser.add_argument(
        "--duration",
        type=float,
        default=float(os.environ.get("LOADGEN_DURATION", "0")),
        help="Seconds to run; 0 means until SIGTERM",
    )
    parser.add_argument(
        "--mix",
        default=os.environ.get("LOADGEN_MIX", "work:2,work/checkout:1"),
    )
    parser.add_argument(
        "--output",
        default=os.environ.get("LOADGEN_OUTPUT", "/results/loadgen.jsonl"),
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
