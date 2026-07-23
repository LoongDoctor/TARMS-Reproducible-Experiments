'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const test = require('node:test');

const TarmsContract = require('../lib/tarms-contract');


const AID_TAG = 'TARMS-ANCHOR-v1\0';
const UPDATE_TAG = 'TARMS-UPDATE-v1\0';


class MemoryStub {
  constructor() {
    this.state = new Map();
  }

  createCompositeKey(objectType, attributes) {
    return `${objectType}\u0000${attributes.join('\u0000')}\u0000`;
  }

  async getState(key) {
    return this.state.get(key) || Buffer.alloc(0);
  }

  async putState(key, value) {
    this.state.set(key, Buffer.from(value));
  }
}


function context(mspId = 'Org1MSP', role = 'anchor-writer') {
  return {
    stub: new MemoryStub(),
    clientIdentity: {
      getMSPID: () => mspId,
      getAttributeValue: (name) => (name === 'tarms.role' ? role : null),
    },
  };
}


function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value);
  }
  if (Number.isSafeInteger(value)) return String(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}


function digest(tag, value) {
  return crypto.createHash('sha256').update(tag).update(canonicalJson(value)).digest('hex');
}


function body(version, previousAid = '') {
  return {
    kappa: 'patient-1|2026-07-22',
    version,
    root: crypto.createHash('sha256').update(`root-${version}`).digest('hex'),
    prevAid: previousAid,
    recordCount: 64,
    uriHash: 'c'.repeat(64),
    createdAt: `2026-07-22T00:0${version - 1}:00Z`,
  };
}


function evidence(anchorBody, previousRoot = '') {
  const newAid = digest(AID_TAG, anchorBody);
  const relation = {
    kappa: anchorBody.kappa,
    previousAid: anchorBody.prevAid,
    previousRoot,
    previousVersion: anchorBody.version - 1,
    newAid,
    newRoot: anchorBody.root,
    newVersion: anchorBody.version,
  };
  return { ...relation, relationDigest: digest(UPDATE_TAG, relation) };
}


async function install(contract, ctx, anchorBody, expectedAid, expectedVersion, previousRoot) {
  return JSON.parse(
    await contract.InstallAnchorCAS(
      ctx,
      JSON.stringify(anchorBody),
      expectedAid,
      String(expectedVersion),
      JSON.stringify(evidence(anchorBody, previousRoot))
    )
  );
}


test('atomically installs a content-addressed version-1 anchor and latest pointer', async () => {
  const contract = new TarmsContract();
  const ctx = context();
  const anchorBody = body(1);
  const expectedAid = digest(AID_TAG, anchorBody);

  const result = await install(contract, ctx, anchorBody, '', 0, '');

  assert.equal(result.aid, expectedAid);
  assert.equal(result.version, 1);
  assert.equal(JSON.parse(await contract.ReadLatest(ctx, anchorBody.kappa)).aid, expectedAid);
  assert.deepEqual(JSON.parse(await contract.ReadAnchor(ctx, expectedAid)), {
    aid: expectedAid,
    ...anchorBody,
  });

  const replay = await install(contract, ctx, anchorBody, '', 0, '');
  assert.equal(replay.result, 'IDEMPOTENT');

  const successorBody = body(2, expectedAid);
  const successor = await install(
    contract, ctx, successorBody, expectedAid, 1, anchorBody.root
  );
  assert.equal(successor.version, 2);
  assert.equal(successor.aid, digest(AID_TAG, successorBody));
});


test('stale CAS is rejected before writing an orphan anchor', async () => {
  const contract = new TarmsContract();
  const ctx = context();
  const firstBody = body(1);
  const first = await install(contract, ctx, firstBody, '', 0, '');
  const secondBody = body(2, first.aid);
  const secondAid = digest(AID_TAG, secondBody);
  const stateSize = ctx.stub.state.size;

  await assert.rejects(
    install(contract, ctx, secondBody, 'stale-aid', 1, firstBody.root),
    /CAS_CONFLICT/
  );

  assert.equal(ctx.stub.state.size, stateSize);
  await assert.rejects(contract.ReadAnchor(ctx, secondAid), /NOT_FOUND/);
  assert.equal(JSON.parse(await contract.ReadLatest(ctx, firstBody.kappa)).aid, first.aid);
});


test('invalid update evidence and unauthorized writers leave no state', async () => {
  const contract = new TarmsContract();
  const authorized = context();
  const anchorBody = body(1);
  const badEvidence = { ...evidence(anchorBody, ''), relationDigest: '0'.repeat(64) };

  await assert.rejects(
    contract.InstallAnchorCAS(
      authorized,
      JSON.stringify(anchorBody),
      '',
      '0',
      JSON.stringify(badEvidence)
    ),
    /UPDATE_EVIDENCE/
  );
  assert.equal(authorized.stub.state.size, 0);

  const unauthorized = context('OutsideMSP');
  await assert.rejects(
    contract.InstallAnchorCAS(
      unauthorized,
      JSON.stringify(anchorBody),
      '',
      '0',
      JSON.stringify(evidence(anchorBody, ''))
    ),
    /ACCESS_DENIED/
  );
  assert.equal(unauthorized.stub.state.size, 0);

  const missingRole = context('Org1MSP', null);
  await assert.rejects(
    contract.InstallAnchorCAS(
      missingRole,
      JSON.stringify(anchorBody),
      '',
      '0',
      JSON.stringify(evidence(anchorBody, ''))
    ),
    /ACCESS_DENIED/
  );
  assert.equal(missingRole.stub.state.size, 0);
});


test('update evidence binds the complete content-derived successor identifier', async () => {
  const contract = new TarmsContract();
  const ctx = context();
  const originalBody = body(1);
  const changedBody = { ...originalBody, recordCount: originalBody.recordCount + 1 };

  await assert.rejects(
    contract.InstallAnchorCAS(
      ctx,
      JSON.stringify(changedBody),
      '',
      '0',
      JSON.stringify(evidence(originalBody, ''))
    ),
    /UPDATE_EVIDENCE/
  );
  assert.equal(ctx.stub.state.size, 0);
});


test('skipped versions and an incorrect previous root are rejected before writes', async () => {
  const contract = new TarmsContract();
  const ctx = context();

  await assert.rejects(install(contract, ctx, body(2), '', 0, ''), /VERSION_CONTINUITY/);
  assert.equal(ctx.stub.state.size, 0);

  const firstBody = body(1);
  const first = await install(contract, ctx, firstBody, '', 0, '');
  const secondBody = body(2, first.aid);
  const before = ctx.stub.state.size;
  await assert.rejects(
    install(contract, ctx, secondBody, first.aid, 1, 'f'.repeat(64)),
    /UPDATE_EVIDENCE/
  );
  assert.equal(ctx.stub.state.size, before);
});


test('admission key includes keyver and supports idempotent retransmission', async () => {
  const contract = new TarmsContract();
  const ctx = context();

  const first = JSON.parse(
    await contract.AcceptOnce(ctx, 'dev-1', '3', 'boot-a', '7', 'digest-original')
  );
  const replay = JSON.parse(
    await contract.AcceptOnce(ctx, 'dev-1', '3', 'boot-a', '7', 'digest-original')
  );
  const rotated = JSON.parse(
    await contract.AcceptOnce(ctx, 'dev-1', '4', 'boot-a', '7', 'digest-rotated')
  );

  assert.equal(first.result, 'NEW');
  assert.equal(replay.result, 'IDEMPOTENT');
  assert.equal(rotated.keyver, 4);
  await assert.rejects(
    contract.AcceptOnce(ctx, 'dev-1', '3', 'boot-a', '7', 'digest-conflict'),
    /COUNTER_CONFLICT/
  );
  assert.equal(
    JSON.parse(await contract.ReadAcceptOnce(ctx, 'dev-1', '3', 'boot-a', '7')).eventDigest,
    'digest-original'
  );
});
