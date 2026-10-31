# PERCH - Automated Canary Release System

A canary release system that automatically promotes good deployments and rolls back bad ones

## What It Does

PERCH runs two versions of your service (stable and canary) behind nginx with weighted routing. A controller continuously monitors metrics and adjusts traffic:

- **Good canary** → Automatically promoted to 100% traffic
- **Bad canary** → Automatically rolled back to 0% before most users see it

**Key Features:**
- Statistical rollback detection using Wilson intervals
- Progressive rollout: 5% → 10% → 25% → 50% → 100%
- Metrics: error rate and p95 latency via Prometheus
- Zero human intervention required

## Quick Start

```bash
# Run a good deployment (promotes to 100%) — brings the stack up itself
make demo-good        # or: python scenarios/run.py good

# Run a bad deployment (rolls back at 5%)
make demo-bad         # or: python scenarios/run.py bad-error

# View the latest run's plot; every run is also archived under results/archive/
open results/rollout.png
```

Each scenario run, with no manual steps:

1. Stops any controller / load generator still running, sets the proxy to 100% stable, and moves
   aside stray result files — so a previous run can never leak into this one.
2. Deploys the scenario's canary (env vars + a recreated container).
3. Starts the load generator, *then* the controller, so the first 5% stage bakes under real traffic.
4. Waits for the controller to promote or roll back.
5. Stops both and archives the run to `results/archive/<scenario>_<timestamp>/`, with a plot and a
   `summary.json`.

Scenarios are safe to run back to back (e.g. `make demo-bad` twice in a row).

**How weights reach nginx**: the controller and the proxy share the `./proxy-conf` folder. The
controller renders `proxy/nginx.conf.j2` into `proxy-conf/default.conf`; the proxy polls that file
once a second and runs `nginx -t && nginx -s reload` when it changes. Writes are debounced
(at most one per `PROXY_RELOAD_DEBOUNCE_SECONDS`) so reloads can't pile up and disturb latency.
Every response carries an `X-Perch-Weights: <stable>/<canary>` header showing the weights nginx
has actually loaded.

**Components:**
- **Stable/Canary Services**: FastAPI apps with configurable faults
- **nginx**: Weighted routing between services (auto-reloads on config changes)
- **Prometheus**: Scrapes metrics every 2s
- **Controller**: Polls every 5s, decides hold/advance/rollback
- **Load Generator**: Optional traffic generator for demos

## How It Decides

The controller uses statistical tests to avoid false alarms:

1. **Sample size check**: Requires ≥40 canary requests before deciding (no noise-based rollbacks)
2. **Error rate**: Uses Wilson interval to test if canary error rate is significantly worse than stable
3. **Latency**: Rolls back if canary p95 > 1.5× stable p95
4. **Borderline cases**: Holds when data is ambiguous rather than guessing

**Why Statistics?**
- Point estimates (e.g., "5% worse") are noisy on small samples
- Wilson intervals provide confidence bounds
- Prevents rollbacks from random unlucky requests

## Demo Scenarios

| Scenario | Fault | Result |
|----------|-------|--------|
| `good` | None | Promotes to 100% (~2 min) |
| `bad-error` | 30% errors | Rolls back at 5% |
| `bad-latency` | 10× slower | Rolls back at 5% |
| `bad-slow-onset` | Errors ramp over 120s | Rolls back during ramp |
| `bad-partial` | Only /work/checkout broken | Rolls back at 5% |

## Configuration

Key settings (via environment variables or `.env`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `RAMP_STAGES` | `5,10,25,50,100` | Traffic percentages |
| `RAMP_BAKE_SECONDS` | `25` | Seconds to hold at each stage |
| `MIN_SAMPLES_PER_WINDOW` | `40` | Minimum samples for decision |
| `LATENCY_TOLERANCE` | `1.5` | p95 rollback ratio |
| `DECISION_MODE` | `tier1` | `tier1`, `naive_threshold`, or `tier2_sprt` |
| `LOADGEN_RPS` | `50` | Traffic rate for demos |

## Understanding the Output

While a run is live, the controller and load generator write into `results/`. When it ends, the
runner moves everything into `results/archive/<scenario>_<timestamp>/` (committed to git as
evidence):

| File | Contents |
|------|----------|
| `decision_log.jsonl` | Every controller decision, one JSON object per line, with the evidence behind it |
| `loadgen.jsonl` | Every request the load generator sent: path, status, latency, which version served it |
| `status.json` | The controller's final state (weight, promoted / rolled back, last reason) |
| `summary.json` | Outcome, decision mode, time to promote/rollback, and blast radius (requests the canary served) |
| `rollout.png` | Three stacked charts: canary weight, error rate, p95 latency, with rollback/promote marked |

`results/rollout.png` is a copy of the latest run's plot. Re-plot any run with
`make plots RUN=results/archive/<run>`. Logs from a controller started outside the runner
(e.g. by `make up`) are moved to `results/archive/unscripted_*` and not committed.

## Troubleshooting

**Controller not making decisions?**
- Check: `docker compose logs controller`
- Ensure services are healthy: `curl http://localhost:8091/health` (or your configured STABLE_PORT)
- Verify Prometheus is scraping: `curl http://localhost:9095/api/v1/targets`
- Check nginx is reloading: `curl -sI http://localhost:8089/health | grep X-Perch-Weights` shows
  the weights nginx has loaded; `docker compose logs proxy` shows reload events

**Rollback happens too quickly?**
- Increase `MIN_SAMPLES_PER_WINDOW` (default: 40)
- Increase `LATENCY_TOLERANCE` (default: 1.5)
- Check if canary actually has higher latency (warm-up time)

**Docker port conflicts?**
- Edit `.env` to change ports:
  ```
  PROXY_PORT=8081
  STABLE_PORT=8093
  CANARY_PORT=8094
  PROMETHEUS_PORT=9096
  ```
- Scenario runners will respect these port settings

## Development

```bash
# Run unit tests (no Docker)
make unit

# Run end-to-end tests against a running stack (`make up` first; not while a scenario is running —
# they rewrite the proxy's weights and check traffic shifts accordingly)
make e2e

# Format code
python -m black .

# Lint code
python -m ruff check .
```

## Production Use

To use PERCH with your services:

1. **Add Prometheus metrics** to your services:
   ```python
   from prometheus_client import Counter, Histogram
   requests = Counter('http_requests_total', 'Total requests', ['service', 'status'])
   latency = Histogram('http_request_duration_seconds', 'Latency', ['service'])
   ```

2. **Configure Prometheus** to scrape your services

3. **Point nginx** to your services in the template

4. **Tune thresholds** for your traffic patterns

## Performance Tuning

**Faster rollouts:**
```bash
RAMP_BAKE_SECONDS=15
MIN_SAMPLES_PER_WINDOW=20
LOADGEN_RPS=100
```

**Safer rollouts:**
```bash
RAMP_BAKE_SECONDS=60
MIN_SAMPLES_PER_WINDOW=100
RAMP_STAGES=1,2,5,10,20,40,60,80,100
LATENCY_TOLERANCE=1.2
```

