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
# Start the stack
docker compose up -d --build

# Run a good deployment (promotes to 100%)
python scenarios/run.py good

# Run a bad deployment (rolls back at 5%)
python scenarios/run.py bad-error

# View the rollout visualization
open results/rollout.png
```

**Note**: The nginx proxy automatically reloads its configuration when the controller adjusts traffic weights. This happens inside the proxy container, so no manual reload step is required.
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

**Decision Log** (`results/decision_log.jsonl`):
- Complete history of every decision with evidence
- Includes metrics from both services
- Shows why each action was taken

**Status File** (`results/status.json`):
- Current state for monitoring
- Quick check of weight and terminal flags

**Rollout Plot** (`results/rollout.png`):
- Three stacked charts: weight, error rate, latency
- Rollback and promote events marked

## Troubleshooting

**Controller not making decisions?**
- Check: `docker compose logs controller`
- Ensure services are healthy: `curl http://localhost:8091/health` (or your configured STABLE_PORT)
- Verify Prometheus is scraping: `curl http://localhost:9095/api/v1/targets`
- Check nginx is reloading: `docker compose logs proxy` should show reload events when config changes

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
# Run unit tests
python -m pytest tests/test_decision.py tests/test_ramp.py tests/test_proxy_writer.py -v

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

