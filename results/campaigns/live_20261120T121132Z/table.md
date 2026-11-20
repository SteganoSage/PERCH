Stats over runs that reached the expected outcome: mean ± sd (sample variance; min–max). Time is from the controller's first decision; canary requests is the blast radius.

| scenario | mode | expected outcome | time to outcome, s | canary requests | canary errors served |
|---|---|---|---|---|---|
| good | naive_threshold | 5/5 | 136.9 ± 2.3 (var 5.2; 134.1–139.8) | 2883.6 ± 24.2 (var 585.3; 2857–2916) | 32.0 ± 5.9 (var 35.0; 25–39) |
| good | tier1 | 5/5 | 138.3 ± 0.7 (var 0.4; 137.1–138.6) | 2836.0 ± 50.8 (var 2576.0; 2746–2869) | 34.0 ± 6.8 (var 46.5; 23–40) |
| good | tier2_sprt | 5/5 | 140.9 ± 3.8 (var 14.5; 137.6–146.3) | 2891.0 ± 30.8 (var 950.0; 2852–2924) | 30.8 ± 7.4 (var 54.2; 23–39) |
| bad-error | naive_threshold | 5/5 | 6.4 ± 2.4 (var 5.7; 4.3–10) | 18.0 ± 6.9 (var 47.0; 13–26) | 4.2 ± 1.3 (var 1.7; 3–6) |
| bad-error | tier1 | 5/5 | 18.6 ± 1.2 (var 1.5; 17.7–20) | 50.6 ± 1.5 (var 2.3; 48–52) | 15.0 ± 3.4 (var 11.5; 9–17) |
| bad-error | tier2_sprt | 5/5 | 8.6 ± 2.9 (var 8.3; 5–12.7) | 25.2 ± 8.8 (var 78.2; 13–38) | 6.6 ± 1.3 (var 1.8; 5–8) |
| bad-latency | naive_threshold | 5/5 | 4.5 ± 1.0 (var 1.1; 2.7–5) | 12.6 ± 0.5 (var 0.3; 12–13) | 0.0 ± 0.0 (var 0.0; 0–0) |
| bad-latency | tier1 | 5/5 | 18.6 ± 1.3 (var 1.6; 17.7–20) | 51.0 ± 0.7 (var 0.5; 50–52) | 0.4 ± 0.5 (var 0.3; 0–1) |
| bad-latency | tier2_sprt | 5/5 | 4.5 ± 1.0 (var 1.1; 2.7–5) | 12.6 ± 0.5 (var 0.3; 12–13) | 0.4 ± 0.5 (var 0.3; 0–1) |
| bad-slow-onset | naive_threshold | 5/5 | 16.0 ± 13.1 (var 172.6; 5–32.7) | 47.6 ± 39.1 (var 1532.3; 13–102) | 4.0 ± 2.8 (var 8.0; 1–7) |
| bad-slow-onset | tier1 | 5/5 | 27.7 ± 9.3 (var 86.4; 17.7–40.4) | 83.6 ± 37.9 (var 1433.3; 50–140) | 6.4 ± 3.8 (var 14.3; 4–13) |
| bad-slow-onset | tier2_sprt | 5/5 | 33.2 ± 9.1 (var 82.1; 20–40.4) | 89.8 ± 27.3 (var 744.7; 51–113) | 6.6 ± 1.9 (var 3.8; 4–9) |
| bad-partial | naive_threshold | 5/5 | 7.5 ± 2.5 (var 6.3; 5–10) | 20.0 ± 6.9 (var 47.5; 12–26) | 3.8 ± 1.5 (var 2.2; 2–6) |
| bad-partial | tier1 | 5/5 | 18.2 ± 1.0 (var 1.1; 17.7–20) | 50.2 ± 1.3 (var 1.7; 49–52) | 11.0 ± 2.6 (var 7.0; 8–15) |
| bad-partial | tier2_sprt | 5/5 | 7.5 ± 2.5 (var 6.3; 5–10) | 20.2 ± 6.6 (var 43.2; 13–25) | 5.0 ± 2.9 (var 8.5; 0–7) |
