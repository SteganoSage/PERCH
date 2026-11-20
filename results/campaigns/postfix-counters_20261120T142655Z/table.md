Stats over runs that reached the expected outcome: mean ± sd (sample variance; min–max). Time is from the controller's first decision; canary requests is the blast radius.

| scenario | mode | expected outcome | time to outcome, s | canary requests | canary errors served |
|---|---|---|---|---|---|
| bad-error | naive_threshold | 5/5 | 5.0 ± 0.0 (var 0.0; 5–5) | 12.6 ± 0.5 (var 0.3; 12–13) | 4.0 ± 1.6 (var 2.5; 2–6) |
| bad-partial | naive_threshold | 5/5 | 5.0 ± 0.0 (var 0.0; 5–5) | 12.6 ± 0.5 (var 0.3; 12–13) | 3.0 ± 1.0 (var 1.0; 2–4) |
