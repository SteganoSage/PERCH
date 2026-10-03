#!/usr/bin/env bash
# Bad canary: only /work/checkout is broken.
set -euo pipefail
cd "$(dirname "$0")/.."
python scenarios/run.py bad-partial
