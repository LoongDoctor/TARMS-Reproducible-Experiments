#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.fabric"
PROFILE="${PROFILE:-submission}"
RESET_NETWORK_BETWEEN_RUNS="${RESET_NETWORK_BETWEEN_RUNS:-1}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Run bootstrap.sh first; missing ${ENV_FILE}" >&2
  exit 2
fi
source "${ENV_FILE}"

if [[ "${PROFILE}" == "submission" ]]; then
  RUNS=5
  WARMUP=10
  DURATION=60
  BATCH_SIZES=(16 64 256 1024 2048 4096)
  CONCURRENCY=(1 4 8 16 32)
  HOT_WRITERS=(2 4 8 16 32)
elif [[ "${PROFILE}" == "smoke" ]]; then
  RUNS=1
  WARMUP=0
  DURATION=2
  BATCH_SIZES=(16 256)
  CONCURRENCY=(1 4)
  HOT_WRITERS=(2 4)
else
  echo "PROFILE must be submission or smoke" >&2
  exit 2
fi

run_case() {
  local workload="$1"
  local concurrency="$2"
  local record_count="$3"
  local round="$4"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local run_id="fabric-${workload}-r${round}-c${concurrency}-n${record_count}-${stamp}"
  local output_dir="${EXPERIMENTS_ROOT}/results/raw/fabric/${run_id}"
  mkdir -p "${output_dir}"
  node "${EXPERIMENTS_ROOT}/fabric/client/src/cli.js" \
    --workload "${workload}" \
    --duration-seconds "${DURATION}" \
    --warmup-seconds "${WARMUP}" \
    --concurrency "${concurrency}" \
    --record-count "${record_count}" \
    --max-attempts 5 \
    --run-id "${run_id}" \
    --hot-key "${run_id}|hot" \
    --output "${output_dir}/fabric_observations.jsonl" \
    --manifest "${output_dir}/run_manifest.json"
}

for round in $(seq 1 "${RUNS}"); do
  if [[ "${round}" -gt 1 && "${RESET_NETWORK_BETWEEN_RUNS}" == "1" ]]; then
    FABRIC_SAMPLES_DIR="${FABRIC_SAMPLES_DIR:?must remain exported}" \
      "${SCRIPT_DIR}/bootstrap.sh"
    source "${ENV_FILE}"
  fi
  for size in "${BATCH_SIZES[@]}"; do
    run_case anchor_submit 1 "${size}" "${round}"
  done
  run_case query 1 64 "${round}"
  for concurrency in "${CONCURRENCY[@]}"; do
    run_case concurrency "${concurrency}" 64 "${round}"
  done
  for writers in "${HOT_WRITERS[@]}"; do
    run_case hot_key_cas "${writers}" 64 "${round}"
  done
done

echo "Fabric ${PROFILE} experiment matrix completed under ${EXPERIMENTS_ROOT}/results/raw/fabric"
