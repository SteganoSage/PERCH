#!/usr/bin/env bash
# Bad canary: latency only, error rate unchanged.
set -euo pipefail
cd "$(dirname "$0")/.."
python scenarios/run.py bad-latency
