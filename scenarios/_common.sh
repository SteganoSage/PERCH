#!/usr/bin/env bash
# Shared scenario plumbing. The real runner is scenarios/run.py (Windows-friendly).
set -euo pipefail
cd "$(dirname "$0")/.."

scenario_run() {
    local name="$1"
    shift || true
    python scenarios/run.py "$name"
}
