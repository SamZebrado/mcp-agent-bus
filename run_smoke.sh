#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHONPATH="$PWD" python3 scripts/smoke_two_agents.py
PYTHONPATH="$PWD" python3 -m unittest discover -s tests
