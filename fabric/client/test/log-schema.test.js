'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  REQUIRED_FIELDS,
  createObservation,
  validateObservation,
} = require('../src/log-schema');


test('creates a complete measured Fabric observation', () => {
  const row = createObservation({
    runId: 'fabric-r1',
    workload: 'anchor_submit',
    clientId: 'client-03',
    operation: 'InstallAnchorCAS',
    startNs: 10n,
    endNs: 110n,
    txid: 'tx-1',
    commitStatus: 'VALID',
    blockNumber: 9n,
    attempt: 1,
    errorClass: '',
    payloadBytes: 256,
    provenance: 'measured_fabric',
  });

  assert.deepEqual(Object.keys(row), REQUIRED_FIELDS);
  assert.equal(row.duration_ns, 100);
  assert.equal(row.block_number, 9);
  assert.equal(validateObservation(row), row);
});


test('rejects impossible timing and fixture submission labels', () => {
  assert.throws(
    () =>
      createObservation({
        runId: 'r', workload: 'w', clientId: 'c', operation: 'o',
        startNs: 20n, endNs: 10n, txid: '', commitStatus: 'ERROR',
        blockNumber: -1n, attempt: 1, errorClass: 'test', payloadBytes: 0,
        provenance: 'measured_fabric',
      }),
    /end_ns/
  );

  const fixture = {
    run_id: 'r', workload: 'w', client_id: 'c', operation: 'o',
    start_ns: 1, end_ns: 2, duration_ns: 1, txid: '', commit_status: 'VALID',
    block_number: 1, attempt: 1, error_class: '', payload_bytes: 1,
    provenance: 'fixture',
  };
  assert.throws(() => validateObservation(fixture, { submission: true }), /fixture/);
});
