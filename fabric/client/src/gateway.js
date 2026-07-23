'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');


const REQUIRED_ENV = [
  'FABRIC_MSP_ID',
  'FABRIC_CERT_PATH',
  'FABRIC_KEY_PATH',
  'FABRIC_TLS_CERT_PATH',
  'FABRIC_PEER_ENDPOINT',
  'FABRIC_PEER_HOST_ALIAS',
  'FABRIC_CHANNEL',
  'FABRIC_CHAINCODE',
];


function loadConnectionMaterial(environment = process.env) {
  for (const name of REQUIRED_ENV) {
    if (!environment[name]) {
      throw new Error(`missing required environment variable ${name}`);
    }
  }
  return {
    mspId: environment.FABRIC_MSP_ID,
    credentials: fs.readFileSync(environment.FABRIC_CERT_PATH),
    privateKeyPem: fs.readFileSync(environment.FABRIC_KEY_PATH),
    tlsRootCert: fs.readFileSync(environment.FABRIC_TLS_CERT_PATH),
    peerEndpoint: environment.FABRIC_PEER_ENDPOINT,
    peerHostAlias: environment.FABRIC_PEER_HOST_ALIAS,
    channelName: environment.FABRIC_CHANNEL,
    chaincodeName: environment.FABRIC_CHAINCODE,
  };
}


function deadlineAfter(milliseconds) {
  return () => ({ deadline: Date.now() + milliseconds });
}


function connectGateway(material, timeoutMs = 15_000) {
  const grpc = require('@grpc/grpc-js');
  const {
    StatusNames,
    connect,
    hash,
    signers,
  } = require('@hyperledger/fabric-gateway');

  const privateKey = crypto.createPrivateKey(material.privateKeyPem);
  const signer = signers.newPrivateKeySigner(privateKey);
  const grpcClient = new grpc.Client(
    material.peerEndpoint,
    grpc.credentials.createSsl(material.tlsRootCert),
    { 'grpc.ssl_target_name_override': material.peerHostAlias }
  );
  const gateway = connect({
    client: grpcClient,
    identity: { mspId: material.mspId, credentials: material.credentials },
    signer,
    hash: hash.sha256,
    evaluateOptions: deadlineAfter(timeoutMs),
    endorseOptions: deadlineAfter(timeoutMs),
    submitOptions: deadlineAfter(timeoutMs),
    commitStatusOptions: deadlineAfter(timeoutMs),
  });
  const contract = gateway
    .getNetwork(material.channelName)
    .getContract(material.chaincodeName);

  return {
    async submit(operation, args) {
      const proposal = contract.newProposal(operation, { arguments: args.map(String) });
      const transaction = await proposal.endorse();
      const commit = await transaction.submit();
      const status = await commit.getStatus();
      const statusName = StatusNames[status.code] || String(status.code);
      if (!status.successful) {
        const error = new Error(`${statusName}: transaction ${status.transactionId} failed`);
        error.transactionId = status.transactionId;
        throw error;
      }
      return {
        result: Buffer.from(transaction.getResult()),
        txid: transaction.getTransactionId(),
        commitStatus: statusName,
        blockNumber: status.blockNumber,
        payloadBytes: Buffer.byteLength(JSON.stringify(args)),
      };
    },

    async evaluate(operation, args) {
      const result = await contract.evaluateTransaction(operation, ...args.map(String));
      return {
        result: Buffer.from(result),
        txid: '',
        commitStatus: 'EVALUATED',
        blockNumber: -1n,
        payloadBytes: Buffer.byteLength(JSON.stringify(args)),
      };
    },

    close() {
      gateway.close();
      grpcClient.close();
    },
  };
}


module.exports = { connectGateway, loadConnectionMaterial };
