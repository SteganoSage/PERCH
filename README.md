# PERCH - Automated Canary Release System

A canary release system that automatically promotes good deployments and rolls back bad ones

## What It Does

PERCH runs two versions of your service (stable and canary) behind nginx with weighted routing. A controller continuously monitors metrics and adjusts traffic:

- **Good canary** → Automatically promoted to 100% traffic
- **Bad canary** → Automatically rolled back to 0% before most users see it

**Key Features:**
- Three interchangeable decision rules: a naive threshold baseline, fixed-sample confidence-interval
  tests, and sequential probability ratio tests (SPRT)
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

Every poll (5s) the controller compares the canary against stable on two signals, error rate and
latency, using one of three rules (`DECISION_MODE`):

| Mode | Error rate | Latency |
|------|------------|---------|
| `naive_threshold` | Roll back if canary is >5 points worse than stable (point estimates) | Roll back if canary p95 > 1.5× stable's |
| `tier1` (default) | Wilson 95% intervals on both error rates over the last 25s; roll back if they don't overlap. Needs ≥40 canary requests | Canary p95 vs 1.5× stable's, also needs ≥40 requests |
| `tier2_sprt` | Wald sequential test on each request's outcome, accumulated across polls: is the canary at stable's error rate, or 5 points worse? | The same kind of test on the share of requests slower than 1.5× stable's p95 |

`tier1` and `tier2_sprt` judge each signal separately and combine the verdicts with one rule:
roll back if any signal is worse, hold if any is still inconclusive, otherwise advance. The
decision log records every signal's verdict and the numbers behind it.

`tier2_sprt` runs fresh tests at each ramp stage and splits the 5% false-rollback budget across
all of them (2 signals × 5 stages), so a good canary's chance of being rolled back anywhere in
the rollout stays under 5%. It needs no minimum sample size: strong evidence (e.g. 6 errors in
12 requests against stable's 1%) decides at once, weak evidence waits.

Both versions fail 1% of requests by default (`BASE_ERROR_RATE`), as real services do. At exactly
0% errors a naive threshold can never false-alarm, which would hide its main weakness.

**Measured without Docker**: `python analysis/simulate_rollouts.py` runs thousands of rollouts
through the same decision and ramp code with synthetic traffic, and reports, per scenario and
mode, how often a good canary is wrongly rolled back and how many requests a bad one reaches
before rollback. At the defaults (1,000 runs each):

| | `naive_threshold` | `tier1` | `tier2_sprt` |
|---|---|---|---|
| **Good canary wrongly rolled back** | 12.5% | 6.8% | **1.2%** |
| Canary requests before rollback (mean ± sd): | | | |
| `bad-error` (30% errors) | 12 ± 2 | 50 ± 0 | 17 ± 7 |
| `bad-latency` (10× slower) | 12 ± 0 | 50 ± 0 | 12 ± 0 |
| `bad-partial` (one endpoint broken) | 13 ± 5 | 50 ± 0 | 25 ± 11 |
| `bad-slow-onset` (errors ramp over 120s) | 77 ± 54 | 77 ± 28 | 97 ± 31 |
| `bad-subtle` (5 points more errors, sim only) | 113 ± 295 | 79 ± 56 | 118 ± 89 |

Every mode caught every bad canary in at least 99.9% of runs; they differ in cost. The naive rule
is fastest but rolls back one good canary in eight. `tier1` pays twice: its 40-request minimum
makes it slowest on loud faults, and re-testing overlapping windows every poll pushes its false
rollbacks past its nominal 5%. `tier2_sprt` keeps false rollbacks near 1%, inside its 5% budget,
while staying close to naive on loud faults. It is slower on gradual and subtle faults: that is
the price of a 5% budget shared across ten tests.

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
| `MIN_SAMPLES_PER_WINDOW` | `40` | Tier 1's minimum canary requests per decision |
| `LATENCY_TOLERANCE` | `1.5` | p95 rollback ratio |
| `DECISION_MODE` | `tier1` | `tier1`, `naive_threshold`, or `tier2_sprt` |
| `BASE_ERROR_RATE` | `0.01` | Background error rate on both versions |
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

**Repeated runs.** `make campaign` (every scenario × every decision mode, 5 runs each, ~2 hours)
and `make baseline` (naive vs the statistical modes on the good and bad-error canaries, ~1 hour)
drive the same runner repeatedly. They record each run as it finishes in
`results/campaigns/<name>_<timestamp>/runs.jsonl`, so `--resume <folder>` picks up an
interrupted campaign, and write the aggregate there: `table.md` (mean ± sd, variance and range of
time to outcome and blast radius per scenario and mode) and, for `make baseline`,
`comparison.md` (live results next to 1,000 simulated runs of the same cells, including how often
each mode wrongly rolls back a good canary). A full campaign already contains the baseline's runs:
`python analysis/compare_baseline_vs_detector.py --summarize <campaign folder>` builds
`comparison.md` from it without running anything again.

`results/rollout.png` is a copy of the latest run's plot. Re-plot any run with
`make plots RUN=results/archive/<run>`. Logs from a controller started outside the runner
(e.g. by `make up`) are moved to `results/archive/unscripted_*` and not committed.

## Experiments and Evaluation

Findings, tables and figures: [`results/experiments/README.md`](results/experiments/README.md).

| Step | Command | Output |
|------|---------|--------|
| Simulation experiments (no Docker, ~1.5 min) | `make experiments` | `results/experiments/simulation/`: E1 every scenario × mode, E2 background noise, E3 fault size, E4 bake time; 1,000 rollouts per cell, CSV + `summary.md` |
| Evaluation tests (~30s) | `make eval` | The detectors' claims as deterministic tests: tier2 stays within its 5% false-rollback budget at 0–5% noise, every mode catches every live fault, tier2 reaches fewer users than tier1 on loud faults, tier1 stalls below its sample floor, … |
| Pre-registration | `predictions/prediction_*.md` | Predicted live results per scenario × mode and eight falsifiable claims, committed before the first live chaos run |
| Live campaign (~2.5 hours) | `make campaign` | `results/campaigns/live_*/`: 75 runs, `table.md` (mean ± sd, variance, range) |
| Score the predictions | `python analysis/evaluate_predictions.py <campaign>` | `evaluation.md`: each claim hit or miss, each cell's live mean vs its predicted range |
| Naive vs statistical | `python analysis/compare_baseline_vs_detector.py --summarize <campaign>` | `comparison.md`: both directions, live next to simulated |
| Figures | `make figures CAMPAIGN=<campaign>` | `results/experiments/figures/*.png` |

Every simulated number is reproducible: each cell's seed is derived from a fixed base seed, so
rerunning gives identical results.

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

# Simulate thousands of rollouts per scenario and decision mode (no Docker, ~40s)
python analysis/simulate_rollouts.py --runs 1000

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

