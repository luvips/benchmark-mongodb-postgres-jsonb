#!/usr/bin/env bash
# Orquestador de la matriz de mediciones (MongoDB vs PostgreSQL JSONB bajo contención cgroups v2).
# Uso:
#   ./run_benchmarks.sh {start|pause [--now]|resume|status|doctor|summarize}
#   ./run_benchmarks.sh validate-data|load-data [--file ruta]
#   ./run_benchmarks.sh run-one --engine {mongodb,postgresql} --profile {A,B,C} --query {Q1,Q2,Q1_FLOAT8} --concurrency {1,10,50} [--variant {strict,relaxed}]
#   ./run_benchmarks.sh --rehearsal <comando>
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [[ "${1:-}" == "--rehearsal" ]]; then
  shift
  export BENCH_REHEARSAL=1 BENCH_STATE_DIR=state-rehearsal BENCH_RESULTS_DIR=results-rehearsal
  export BENCH_PHASE_SCALE="${BENCH_PHASE_SCALE:-0.05}" BENCH_DATA="${BENCH_DATA:-data/fixture.ndjson}"
fi

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x ".venv/Scripts/python.exe" ]]; then
  PYTHON_BIN=".venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

exec "$PYTHON_BIN" -m orchestrator.cli "$@"

