Live: mean ± sd over runs that rolled back. Simulated: 1000 rollouts per cell (analysis/simulate_rollouts.py, seed 20261031), same decision code, 1% background errors.

| mode | bad-error: canary requests (live) | (sim) | bad-error: seconds to rollback (live) | (sim) | good: promoted (live) | good: wrongly rolled back (sim) |
|---|---|---|---|---|---|---|
| naive_threshold | 18.0 ± 6.9 (n=5) | 12.1 ± 0.9 (n=1000) | 6.4 ± 2.4 (n=5) | 5.0 ± 0.4 (n=1000) | 5/5 | 12.5% [10.6, 14.7] |
| tier1 | 50.6 ± 1.5 (n=5) | 50.0 ± 0.0 (n=1000) | 18.6 ± 1.2 (n=5) | 20.0 ± 0.0 (n=1000) | 5/5 | 7.6% [6.1, 9.4] |
| tier2_sprt | 25.2 ± 8.8 (n=5) | 18.0 ± 7.7 (n=1000) | 8.6 ± 2.9 (n=5) | 7.3 ± 3.0 (n=1000) | 5/5 | 0.8% [0.4, 1.6] |

- **tier1 vs naive**: bad-error blast radius 50.6 vs 18.0 canary requests live (50.0 vs 12.1 simulated); good canaries wrongly rolled back 7.6% vs 12.5% (simulated).
- **tier2_sprt vs naive**: bad-error blast radius 25.2 vs 18.0 canary requests live (18.0 vs 12.1 simulated); good canaries wrongly rolled back 0.8% vs 12.5% (simulated).
