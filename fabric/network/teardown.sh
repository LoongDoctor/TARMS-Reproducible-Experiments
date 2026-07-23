#!/usr/bin/env bash
set -euo pipefail

FABRIC_SAMPLES_DIR="${FABRIC_SAMPLES_DIR:-}"
if [[ -z "${FABRIC_SAMPLES_DIR}" ]]; then
  echo "FABRIC_SAMPLES_DIR must point to the Fabric checkout used for this test network" >&2
  exit 2
fi
NETWORK_SCRIPT="${FABRIC_SAMPLES_DIR}/test-network/network.sh"
if [[ ! -x "${NETWORK_SCRIPT}" ]]; then
  echo "Refusing teardown: expected network script not found at ${NETWORK_SCRIPT}" >&2
  exit 2
fi
cd "${FABRIC_SAMPLES_DIR}/test-network"
"${NETWORK_SCRIPT}" down
echo "TARMS Fabric test network stopped; generated experiment logs were preserved."
