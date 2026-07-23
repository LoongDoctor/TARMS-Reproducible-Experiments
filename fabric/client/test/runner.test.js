'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  buildAnchorBody,
  buildUpdateEvidence,
  computeAnchorAid,
  parseArgs,
  setupLatest,
} = require('../src/runner');


test('parses the locked workload controls', () => {
  const options = parseArgs([
    '--workload', 'hot_key_cas',
    '--duration-seconds', '60',
    '--warmup-seconds', '10',
    '--concurrency', '16',
    '--run-id', 'fabric-r1',
    '--output', '/tmp/fabric.jsonl',
  ]);

  assert.equal(options.workload, 'hot_key_cas');
  assert.equal(options.durationSeconds, 60);
  assert.equal(options.warmupSeconds, 10);
  assert.equal(options.concurrency, 16);
  assert.equal(options.runId, 'fabric-r1');
});


test('anchor identifiers are derived from the complete canonical body', () => {
  const first = buildAnchorBody({
    kappa: 'patient-1|day-1', version: 1, root: 'root-a', prevAid: '',
    recordCount: 64, uriHash: 'a'.repeat(64), createdAt: '2026-07-22T00:00:00Z',
  });
  const changed = { ...first, recordCount: 65 };

  assert.match(computeAnchorAid(first), /^[a-f0-9]{64}$/);
  assert.notEqual(computeAnchorAid(first), computeAnchorAid(changed));
  const relation = buildUpdateEvidence(first, '');
  assert.equal(relation.newAid, computeAnchorAid(first));
  assert.equal(relation.previousVersion, 0);
});


test('genesis setup uses one atomic InstallAnchorCAS submission at version 1', async () => {
  const calls = [];
  const adapter = {
    submit: async (operation, args) => {
      calls.push({ operation, args });
      return { result: Buffer.from(JSON.stringify({ aid: computeAnchorAid(JSON.parse(args[0])) })) };
    },
  };

  const aid = await setupLatest(adapter, 'patient-1|day-1', 'run-1', 64);

  assert.match(aid, /^[a-f0-9]{64}$/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, 'InstallAnchorCAS');
  assert.equal(JSON.parse(calls[0].args[0]).version, 1);
  assert.equal(calls[0].args[1], '');
  assert.equal(calls[0].args[2], '0');
});
