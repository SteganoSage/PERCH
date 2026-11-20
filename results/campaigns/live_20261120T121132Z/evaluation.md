# Predictions vs live results

Campaign: `live_20261120T121132Z` (75 runs). Predictions: the committed `predictions/prediction_*.md`. Criteria are transcribed from that file.

## Falsifiable claims: 7/8 hit

| claim | result | numbers |
|---|---|---|
| P1. No bad canary escapes | ✅ hit | 60/60 bad runs rolled back, 0 promoted |
| P2. Loud-fault ordering naive ≤ tier2 < tier1 | ✅ hit | bad-error: naive 18.0, tier2 25.2, tier1 50.6; bad-latency: naive 12.6, tier2 12.6, tier1 51.0; bad-partial: naive 20.0, tier2 20.2, tier1 50.2 |
| P3. tier1's blast radius is its 40-request floor | ✅ hit | bad-error: mean 50.6, sd 1.5; bad-partial: mean 50.2, sd 1.3 |
| P4. The live tax on first-poll detectors | ❌ miss | bad-error/naive_threshold: live − sim 5.8; bad-error/tier2_sprt: live − sim 7.7; bad-error/tier1: live 50.6; bad-partial/naive_threshold: live − sim 7.3; bad-partial/tier2_sprt: live − sim -4.8; bad-partial/tier1: live 50.2 |
| P5. Slow onset is tier2's worst case; naive the most variable | ✅ hit | naive_threshold: 47.6 ± 39.1, tier1: 83.6 ± 37.9, tier2_sprt: 89.8 ± 27.3 |
| P6. tier2 promotes 5/5; naive + tier1 show 0-2 false rollbacks in 10 | ✅ hit | tier2 promoted 5/5; false rollbacks: naive_threshold 0/5, tier1 0/5, tier2_sprt 0/5 |
| P7. Every promoted good run takes 128-150s | ✅ hit | 15 runs, 134.1–146.3s |
| P8. bad-error errors served: naive < tier2 < tier1, tier1 ≈ 3× tier2 | ✅ hit | naive_threshold 4.2, tier1 15.0, tier2_sprt 6.6; tier1/tier2 2.3 |

## Per-cell predicted ranges: 20/30 live means inside

| scenario | mode | runs ok | seconds: predicted → live | in range | canary requests: predicted → live | in range |
|---|---|---|---|---|---|---|
| good | naive_threshold | 5/5 | 132 (128–136) → **136.9** | ❌ | 2420 (2375–2470) → **2883.6** | ❌ |
| good | tier1 | 5/5 | 134 (130–142) → **138.3** | ✅ | 2430 (2380–2500) → **2836.0** | ❌ |
| good | tier2_sprt | 5/5 | 138 (130–150) → **140.9** | ✅ | 2440 (2380–2520) → **2891.0** | ❌ |
| bad-error | naive_threshold | 5/5 | 5 (5–8) → **6.4** | ✅ | 14 (12–20) → **18.0** | ✅ |
| bad-error | tier1 | 5/5 | 20 (20–22) → **18.6** | ❌ | 50 (47–54) → **50.6** | ✅ |
| bad-error | tier2_sprt | 5/5 | 9 (6–12) → **8.6** | ✅ | 22 (15–30) → **25.2** | ✅ |
| bad-latency | naive_threshold | 5/5 | 5 (5–7) → **4.5** | ❌ | 14 (12–18) → **12.6** | ✅ |
| bad-latency | tier1 | 5/5 | 22 (20–25) → **18.6** | ❌ | 55 (48–65) → **51.0** | ✅ |
| bad-latency | tier2_sprt | 5/5 | 5 (5–7) → **4.5** | ❌ | 14 (12–18) → **12.6** | ✅ |
| bad-partial | naive_threshold | 5/5 | 6 (5–10) → **7.5** | ✅ | 15 (12–25) → **20.0** | ✅ |
| bad-partial | tier1 | 5/5 | 20 (20–22) → **18.2** | ❌ | 50 (47–54) → **50.2** | ✅ |
| bad-partial | tier2_sprt | 5/5 | 12 (8–16) → **7.5** | ❌ | 30 (20–42) → **20.2** | ✅ |
| bad-slow-onset | naive_threshold | 5/5 | 27 (15–40) → **16.0** | ✅ | 85 (40–140) → **47.6** | ✅ |
| bad-slow-onset | tier1 | 5/5 | 30 (25–38) → **27.7** | ✅ | 82 (60–110) → **83.6** | ✅ |
| bad-slow-onset | tier2_sprt | 5/5 | 40 (32–50) → **33.2** | ✅ | 105 (80–140) → **89.8** | ✅ |
