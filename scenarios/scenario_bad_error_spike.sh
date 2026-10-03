#!/usr/bin/env bash
# Bad canary: uniformly elevated error rate. Primary rollback demo.
set -euo pipefail
cd "$(dirname "$0")/.."
python scenarios/run.py bad-error
