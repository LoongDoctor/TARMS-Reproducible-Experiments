'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { loadConnectionMaterial } = require('../src/gateway');


test('loads all explicit Gateway connection material', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tarms-gateway-'));
  const cert = path.join(directory, 'cert.pem');
  const key = path.join(directory, 'key.pem');
  const tls = path.join(directory, 'tls.pem');
  fs.writeFileSync(cert, 'CERT');
  fs.writeFileSync(key, 'KEY');
  fs.writeFileSync(tls, 'TLS');

  const material = loadConnectionMaterial({
    FABRIC_MSP_ID: 'Org1MSP',
    FABRIC_CERT_PATH: cert,
    FABRIC_KEY_PATH: key,
    FABRIC_TLS_CERT_PATH: tls,
    FABRIC_PEER_ENDPOINT: 'localhost:7051',
    FABRIC_PEER_HOST_ALIAS: 'peer0.org1.example.com',
    FABRIC_CHANNEL: 'tarmschannel',
    FABRIC_CHAINCODE: 'tarms',
  });

  assert.equal(material.mspId, 'Org1MSP');
  assert.equal(material.credentials.toString(), 'CERT');
  assert.equal(material.privateKeyPem.toString(), 'KEY');
  assert.equal(material.tlsRootCert.toString(), 'TLS');
  assert.equal(material.channelName, 'tarmschannel');
  assert.equal(material.chaincodeName, 'tarms');
});


test('rejects implicit or incomplete connection configuration', () => {
  assert.throws(() => loadConnectionMaterial({ FABRIC_MSP_ID: 'Org1MSP' }), /FABRIC_CERT_PATH/);
});
