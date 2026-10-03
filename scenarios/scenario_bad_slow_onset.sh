#!/usr/bin/env bash
# Bad canary: degradation ramps in gradually over ~120s.
set -euo pipefail
cd "$(dirname "$0")/.."
python scenarios/run.py bad-slow-onset
