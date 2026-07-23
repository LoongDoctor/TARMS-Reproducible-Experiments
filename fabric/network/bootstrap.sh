#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FABRIC_SAMPLES_DIR="${FABRIC_SAMPLES_DIR:-}"
CHANNEL_NAME="${CHANNEL_NAME:-tarmschannel}"
CHAINCODE_NAME="${CHAINCODE_NAME:-tarms}"
CHAINCODE_VERSION="${CHAINCODE_VERSION:-0.1}"

if [[ -z "${FABRIC_SAMPLES_DIR}" ]]; then
  echo "FABRIC_SAMPLES_DIR must point to a Fabric 2.5.16 fabric-samples checkout" >&2
  exit 2
fi
TEST_NETWORK="${FABRIC_SAMPLES_DIR}/test-network"
NETWORK_SCRIPT="${TEST_NETWORK}/network.sh"
if [[ ! -x "${NETWORK_SCRIPT}" ]]; then
  echo "Expected executable not found: ${NETWORK_SCRIPT}" >&2
  exit 2
fi

export PATH="${FABRIC_SAMPLES_DIR}/bin:${PATH}"
export FABRIC_CFG_PATH="${FABRIC_SAMPLES_DIR}/config"
export IMAGE_TAG="2.5.16"
export CA_IMAGE_TAG="1.5.17"

NPM_CONFIG_CACHE="${EXPERIMENTS_ROOT}/.cache/npm" \
  npm --prefix "${EXPERIMENTS_ROOT}/fabric/chaincode" ci --ignore-scripts --no-audit --no-fund
NPM_CONFIG_CACHE="${EXPERIMENTS_ROOT}/.cache/npm" \
  npm --prefix "${EXPERIMENTS_ROOT}/fabric/client" ci --ignore-scripts --no-audit --no-fund

cd "${TEST_NETWORK}"
"${NETWORK_SCRIPT}" down
"${NETWORK_SCRIPT}" up createChannel -ca -c "${CHANNEL_NAME}" -s leveldb
"${NETWORK_SCRIPT}" deployCC \
  -c "${CHANNEL_NAME}" \
  -ccn "${CHAINCODE_NAME}" \
  -ccp "${EXPERIMENTS_ROOT}/fabric/chaincode" \
  -ccl javascript \
  -ccv "${CHAINCODE_VERSION}" \
  -ccs 1 \
  -ccep "AND('Org1MSP.peer','Org2MSP.peer')"

ORG1_ROOT="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com"
ORG1_CA_CERT="${ORG1_ROOT}/ca/ca.org1.example.com-cert.pem"
WRITER_HOME="${ORG1_ROOT}/users/TarmsWriter@org1.example.com"
WRITER_MSP="${WRITER_HOME}/msp"
export FABRIC_CA_CLIENT_HOME="${ORG1_ROOT}"
fabric-ca-client register \
  -u https://localhost:7054 \
  --caname ca-org1 \
  --id.name tarms-writer \
  --id.secret tarms-writer-pw \
  --id.type client \
  --id.attrs 'tarms.role=anchor-writer:ecert' \
  --tls.certfiles "${ORG1_CA_CERT}"
fabric-ca-client enroll \
  -u https://tarms-writer:tarms-writer-pw@localhost:7054 \
  --caname ca-org1 \
  -M "${WRITER_MSP}" \
  --tls.certfiles "${ORG1_CA_CERT}"

CERT_PATH="${WRITER_MSP}/signcerts/cert.pem"
KEY_DIR="${WRITER_MSP}/keystore"
KEY_PATH="$(find "${KEY_DIR}" -maxdepth 1 -type f -print -quit)"
TLS_CERT_PATH="${TEST_NETWORK}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
for required in "${CERT_PATH}" "${KEY_PATH}" "${TLS_CERT_PATH}"; do
  if [[ ! -f "${required}" ]]; then
    echo "Required connection material not found: ${required}" >&2
    exit 3
  fi
done

cat > "${SCRIPT_DIR}/.env.fabric" <<EOF
export FABRIC_SAMPLES_DIR=${FABRIC_SAMPLES_DIR}
export FABRIC_MSP_ID=Org1MSP
export FABRIC_CERT_PATH=${CERT_PATH}
export FABRIC_KEY_PATH=${KEY_PATH}
export FABRIC_TLS_CERT_PATH=${TLS_CERT_PATH}
export FABRIC_PEER_ENDPOINT=localhost:7051
export FABRIC_PEER_HOST_ALIAS=peer0.org1.example.com
export FABRIC_CHANNEL=${CHANNEL_NAME}
export FABRIC_CHAINCODE=${CHAINCODE_NAME}
export FABRIC_VERSION=2.5.16
export FABRIC_CA_VERSION=1.5.17
export FABRIC_NETWORK_ID=tarms-two-org-test-network
EOF

echo "Fabric network ready. Connection environment: ${SCRIPT_DIR}/.env.fabric"
