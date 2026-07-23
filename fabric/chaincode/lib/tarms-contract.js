'use strict';

const crypto = require('node:crypto');
const { Contract } = require('fabric-contract-api');


const AID_TAG = 'TARMS-ANCHOR-v1\0';
const UPDATE_TAG = 'TARMS-UPDATE-v1\0';
const WRITER_MSPS = new Set(['Org1MSP', 'Org2MSP']);
const ANCHOR_FIELDS = [
  'createdAt', 'kappa', 'prevAid', 'recordCount', 'root', 'uriHash', 'version',
];
const EVIDENCE_FIELDS = [
  'kappa', 'newAid', 'newRoot', 'newVersion', 'previousAid', 'previousRoot',
  'previousVersion', 'relationDigest',
];


function nonEmpty(value, name) {
  const text = String(value);
  if (text.length === 0) {
    throw new Error(`INVALID_ARGUMENT: ${name} must be non-empty`);
  }
  return text;
}


function integer(value, name, minimum = 0) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum) {
    throw new Error(`INVALID_ARGUMENT: ${name} must be an integer >= ${minimum}`);
  }
  return parsed;
}


function hexDigest(value, name) {
  const text = String(value).toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(text)) {
    throw new Error(`INVALID_ARGUMENT: ${name} must be a 64-character hexadecimal digest`);
  }
  return text;
}


function exactFields(value, expected, name) {
  if (!value || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`INVALID_ARGUMENT: ${name} must be a JSON object`);
  }
  const found = Object.keys(value).sort();
  if (JSON.stringify(found) !== JSON.stringify([...expected].sort())) {
    throw new Error(`INVALID_ARGUMENT: ${name} fields do not match the locked schema`);
  }
}


function canonicalJson(value) {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') return JSON.stringify(value.normalize('NFC'));
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) {
      throw new Error('INVALID_ARGUMENT: canonical JSON permits only safe integers');
    }
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const normalized = new Map();
    for (const key of Object.keys(value)) {
      const normalizedKey = key.normalize('NFC');
      if (normalized.has(normalizedKey)) {
        throw new Error('INVALID_ARGUMENT: normalized object-key collision');
      }
      normalized.set(normalizedKey, value[key]);
    }
    const keys = [...normalized.keys()].sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(normalized.get(key))}`).join(',')}}`;
  }
  throw new Error('INVALID_ARGUMENT: unsupported canonical JSON value');
}


function taggedDigest(tag, value) {
  return crypto.createHash('sha256').update(tag).update(canonicalJson(value)).digest('hex');
}


function parseJsonObject(text, name) {
  let value;
  try {
    value = JSON.parse(String(text));
  } catch (error) {
    throw new Error(`INVALID_ARGUMENT: ${name} must be valid JSON: ${error.message}`);
  }
  if (!value || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`INVALID_ARGUMENT: ${name} must be a JSON object`);
  }
  return value;
}


function normalizeAnchorBody(value) {
  exactFields(value, ANCHOR_FIELDS, 'anchorBody');
  return {
    kappa: nonEmpty(value.kappa, 'kappa'),
    version: integer(value.version, 'version', 1),
    root: hexDigest(value.root, 'root'),
    prevAid: String(value.prevAid),
    recordCount: integer(value.recordCount, 'recordCount', 1),
    uriHash: hexDigest(value.uriHash, 'uriHash'),
    createdAt: nonEmpty(value.createdAt, 'createdAt'),
  };
}


function normalizeEvidence(value) {
  exactFields(value, EVIDENCE_FIELDS, 'updateEvidence');
  return {
    kappa: nonEmpty(value.kappa, 'updateEvidence.kappa'),
    previousAid: String(value.previousAid),
    previousRoot: String(value.previousRoot),
    previousVersion: integer(
      value.previousVersion, 'updateEvidence.previousVersion'
    ),
    newAid: hexDigest(value.newAid, 'updateEvidence.newAid'),
    newRoot: hexDigest(value.newRoot, 'updateEvidence.newRoot'),
    newVersion: integer(value.newVersion, 'updateEvidence.newVersion', 1),
    relationDigest: hexDigest(value.relationDigest, 'updateEvidence.relationDigest'),
  };
}


class TarmsContract extends Contract {
  constructor() {
    super('org.tarms.records');
  }

  _requireWriter(ctx) {
    const mspId = ctx.clientIdentity && ctx.clientIdentity.getMSPID
      ? ctx.clientIdentity.getMSPID()
      : '';
    const role = ctx.clientIdentity && ctx.clientIdentity.getAttributeValue
      ? ctx.clientIdentity.getAttributeValue('tarms.role')
      : null;
    if (!WRITER_MSPS.has(mspId) || role !== 'anchor-writer') {
      throw new Error(
        `ACCESS_DENIED: MSP ${mspId || '(missing)'} with role ` +
        `${role || '(missing)'} cannot write TARMS state`
      );
    }
  }

  _anchorKey(ctx, aid) {
    return ctx.stub.createCompositeKey('anchor', [nonEmpty(aid, 'aid')]);
  }

  _latestKey(ctx, kappa) {
    return ctx.stub.createCompositeKey('latest', [nonEmpty(kappa, 'kappa')]);
  }

  _acceptKey(ctx, did, keyver, boot, counter) {
    return ctx.stub.createCompositeKey('accept', [
      nonEmpty(did, 'did'),
      String(integer(keyver, 'keyver', 1)),
      nonEmpty(boot, 'boot'),
      String(integer(counter, 'counter')),
    ]);
  }

  async _readJson(ctx, key, notFoundLabel) {
    const value = await ctx.stub.getState(key);
    if (!value || value.length === 0) {
      throw new Error(`NOT_FOUND: ${notFoundLabel}`);
    }
    return JSON.parse(value.toString('utf8'));
  }

  async ReadAnchor(ctx, aid) {
    const anchor = await this._readJson(ctx, this._anchorKey(ctx, aid), `anchor ${aid}`);
    return JSON.stringify(anchor);
  }

  async ReadLatest(ctx, kappa) {
    const latest = await this._readJson(ctx, this._latestKey(ctx, kappa), `latest ${kappa}`);
    return JSON.stringify(latest);
  }

  async InstallAnchorCAS(
    ctx,
    anchorBodyJson,
    expectedAid,
    expectedVersion,
    updateEvidenceJson
  ) {
    this._requireWriter(ctx);
    const body = normalizeAnchorBody(parseJsonObject(anchorBodyJson, 'anchorBody'));
    const evidence = normalizeEvidence(parseJsonObject(updateEvidenceJson, 'updateEvidence'));
    const expectedAidText = String(expectedAid);
    const expectedVersionNumber = integer(expectedVersion, 'expectedVersion');
    const aid = taggedDigest(AID_TAG, body);
    const latestKey = this._latestKey(ctx, body.kappa);
    const latestBytes = await ctx.stub.getState(latestKey);
    const current = latestBytes && latestBytes.length > 0
      ? JSON.parse(latestBytes.toString('utf8'))
      : null;

    const assertEvidence = (previousRoot) => {
      const relation = {
        kappa: body.kappa,
        previousAid: body.prevAid,
        previousRoot,
        previousVersion: body.version - 1,
        newAid: aid,
        newRoot: body.root,
        newVersion: body.version,
      };
      if (
        evidence.kappa !== relation.kappa ||
        evidence.previousAid !== relation.previousAid ||
        evidence.previousRoot !== relation.previousRoot ||
        evidence.previousVersion !== relation.previousVersion ||
        evidence.newAid !== relation.newAid ||
        evidence.newRoot !== relation.newRoot ||
        evidence.newVersion !== relation.newVersion ||
        evidence.relationDigest !== taggedDigest(UPDATE_TAG, relation)
      ) {
        throw new Error('UPDATE_EVIDENCE: update relation or digest is invalid');
      }
    };

    if (current && current.aid === aid && current.version === body.version) {
      if (
        body.version !== expectedVersionNumber + 1 ||
        body.prevAid !== expectedAidText
      ) {
        throw new Error('ANCHOR_MISMATCH: idempotent request has inconsistent predecessor');
      }
      let replayPreviousRoot = '';
      if (body.prevAid !== '') {
        const previous = await this._readJson(
          ctx,
          this._anchorKey(ctx, body.prevAid),
          `anchor ${body.prevAid}`
        );
        replayPreviousRoot = previous.root;
      }
      assertEvidence(replayPreviousRoot);
      const existing = await this._readJson(ctx, this._anchorKey(ctx, aid), `anchor ${aid}`);
      if (canonicalJson(existing) !== canonicalJson({ aid, ...body })) {
        throw new Error(`ANCHOR_MISMATCH: stored content differs for ${aid}`);
      }
      return JSON.stringify({ ...current, result: 'IDEMPOTENT' });
    }

    if (
      (current === null && (expectedAidText !== '' || expectedVersionNumber !== 0)) ||
      (current !== null && (
        current.aid !== expectedAidText || current.version !== expectedVersionNumber
      ))
    ) {
      const found = current === null ? '(none,0)' : `(${current.aid},${current.version})`;
      throw new Error(
        `CAS_CONFLICT: expected (${expectedAidText},${expectedVersionNumber}), found ${found}`
      );
    }
    if (body.version !== expectedVersionNumber + 1) {
      throw new Error(
        `VERSION_CONTINUITY: anchor version ${body.version} must equal expected version ` +
        `${expectedVersionNumber} plus one`
      );
    }
    if (body.prevAid !== expectedAidText) {
      throw new Error('ANCHOR_MISMATCH: prevAid does not match expected latest aid');
    }

    let previousRoot = '';
    if (current !== null) {
      const previous = await this._readJson(
        ctx,
        this._anchorKey(ctx, current.aid),
        `anchor ${current.aid}`
      );
      previousRoot = previous.root;
    }
    assertEvidence(previousRoot);

    const anchorKey = this._anchorKey(ctx, aid);
    const existingBytes = await ctx.stub.getState(anchorKey);
    if (existingBytes && existingBytes.length > 0) {
      throw new Error(`ANCHOR_EXISTS: ${aid}`);
    }
    const anchor = { aid, ...body };
    const latest = {
      kappa: body.kappa,
      aid,
      version: body.version,
      root: body.root,
    };
    await ctx.stub.putState(anchorKey, Buffer.from(canonicalJson(anchor)));
    await ctx.stub.putState(latestKey, Buffer.from(canonicalJson(latest)));
    return JSON.stringify({ ...latest, result: 'NEW' });
  }

  async AcceptOnce(ctx, did, keyver, boot, counter, eventDigest) {
    this._requireWriter(ctx);
    const key = this._acceptKey(ctx, did, keyver, boot, counter);
    const digestValue = nonEmpty(eventDigest, 'eventDigest');
    const existing = await ctx.stub.getState(key);
    if (existing && existing.length > 0) {
      const admission = JSON.parse(existing.toString('utf8'));
      if (admission.eventDigest === digestValue) {
        return JSON.stringify({ ...admission, result: 'IDEMPOTENT' });
      }
      throw new Error(`COUNTER_CONFLICT: ${did}/${keyver}/${boot}/${counter}`);
    }
    const admission = {
      did: nonEmpty(did, 'did'),
      keyver: integer(keyver, 'keyver', 1),
      boot: nonEmpty(boot, 'boot'),
      counter: integer(counter, 'counter'),
      eventDigest: digestValue,
    };
    await ctx.stub.putState(key, Buffer.from(canonicalJson(admission)));
    return JSON.stringify({ ...admission, result: 'NEW' });
  }

  async ReadAcceptOnce(ctx, did, keyver, boot, counter) {
    const admission = await this._readJson(
      ctx,
      this._acceptKey(ctx, did, keyver, boot, counter),
      `admission ${did}/${keyver}/${boot}/${counter}`
    );
    return JSON.stringify(admission);
  }
}


module.exports = TarmsContract;
