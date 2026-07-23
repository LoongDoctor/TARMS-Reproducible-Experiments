'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const { classifyError, measureOperation } = require('../src/workload');


function clockFrom(values) {
  const queue = [...values];
  return () => queue.shift();
}


test('measures a committed operation into the shared JSONL schema', async () => {
  const outcome = await measureOperation({
    metadata: {
      runId: 'r1', workload: 'anchor_submit', clientId: 'c1',
      operation: 'InstallAnchorCAS', attempt: 1, provenance: 'measured_fabric',
    },
    action: async () => ({
      txid: 'tx1', commitStatus: 'VALID', blockNumber: 12n,
      payloadBytes: 128, result: Buffer.from('ok'),
    }),
    clock: clockFrom([100n, 250n]),
  });

  assert.equal(outcome.row.duration_ns, 150);
  assert.equal(outcome.row.commit_status, 'VALID');
  assert.equal(outcome.result.toString(), 'ok');
  assert.equal(outcome.error, null);
});


test('classifies CAS conflicts without throwing away timing evidence', async () => {
  const outcome = await measureOperation({
    metadata: {
      runId: 'r1', workload: 'hot_key_cas', clientId: 'c2',
      operation: 'InstallAnchorCAS', attempt: 2, provenance: 'measured_fabric',
    },
    action: async () => {
      throw new Error('CAS_CONFLICT: expected aid-v1');
    },
    clock: clockFrom([400n, 900n]),
  });

  assert.equal(outcome.row.error_class, 'CAS_CONFLICT');
  assert.equal(outcome.row.commit_status, 'ERROR');
  assert.equal(outcome.error.message, 'CAS_CONFLICT: expected aid-v1');
  assert.equal(classifyError(new Error('VERSION_CONTINUITY: bad')), 'VERSION_CONTINUITY');
});
