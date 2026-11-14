#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

tests/build.sh
tests/gen_legal.py --out validation/legal --count 100
tests/verify_legal.py --dir validation/legal --build validation/build \
	--out validation/results/legal.json
tests/fuzz_campaign.py --build validation/build --out validation/malformed \
	--results validation/results/malformed.json \
	--counterexamples validation/counterexamples
tests/replay_baseline.py --build validation/build --dir validation/malformed \
	--out validation/results/baseline.json
