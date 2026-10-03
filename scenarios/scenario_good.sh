#!/usr/bin/env bash
# Good canary: behaves exactly like stable, should ramp to 100% unattended.
set -euo pipefail
cd "$(dirname "$0")/.."
python scenarios/run.py good
