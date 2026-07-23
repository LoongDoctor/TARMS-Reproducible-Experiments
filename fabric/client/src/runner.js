'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const { measureOperation } = require('./workload');


const WORKLOADS = new Set(['anchor_submit', 'query', 'concurrency', 'hot_key_cas']);


function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!flag || !flag.startsWith('--') || value === undefined) {
      throw new Error(`arguments must be --name value pairs; invalid token ${flag || '(end)'}`);
    }
    values[flag.slice(2)] = value;
  }
  const runId = values['run-id'] || `fabric-${new Date().toISOString().replace(/[-:.]/g, '')}`;
  const options = {
    workload: values.workload || 'anchor_submit',
    durationSeconds: Number(values['duration-seconds'] || 60),
    warmupSeconds: Number(values['warmup-seconds'] || 10),
    concurrency: Number(values.concurrency || 1),
    maxAttempts: Number(values['max-attempts'] || 5),
    recordCount: Number(values['record-count'] || 64),
    runId,
    output: values.output || path.resolve('fabric-results', `${runId}.jsonl`),
    manifest: values.manifest || path.resolve('fabric-results', `${runId}.manifest.json`),
    hotKey: values['hot-key'] || 'patient-hot|2026-07-22',
  };
  if (!WORKLOADS.has(options.workload)) {
    throw new Error(`unknown workload ${options.workload}`);
  }
  for (const name of ['durationSeconds', 'concurrency', 'maxAttempts', 'recordCount']) {
    if (!Number.isInteger(options[name]) || options[name] <= 0) {
      throw new Error(`${name} must be a positive integer`);
    }
  }
  if (!Number.isInteger(options.warmupSeconds) || options.warmupSeconds < 0) {
    throw new Error('warmupSeconds must be a nonnegative integer');
  }
  return options;
}


function canonicalJson(value) {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') return JSON.stringify(value.normalize('NFC'));
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) {
      throw new Error('canonical JSON permits only safe integers');
    }
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const normalized = new Map();
    for (const key of Object.keys(value)) {
      const normalizedKey = key.normalize('NFC');
      if (normalized.has(normalizedKey)) throw new Error('normalized object-key collision');
      normalized.set(normalizedKey, value[key]);
    }
    const keys = [...normalized.keys()].sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(normalized.get(key))}`).join(',')}}`;
  }
  throw new Error('unsupported canonical JSON value');
}


function taggedDigest(tag, value) {
  return crypto.createHash('sha256').update(tag).update(canonicalJson(value)).digest('hex');
}


function buildAnchorBody({
  kappa, version, root, prevAid, recordCount, uriHash, createdAt,
}) {
  return {
    kappa: String(kappa),
    version: Number(version),
    root: String(root),
    prevAid: String(prevAid),
    recordCount: Number(recordCount),
    uriHash: String(uriHash),
    createdAt: String(createdAt),
  };
}


function computeAnchorAid(anchorBody) {
  return taggedDigest('TARMS-ANCHOR-v1\0', anchorBody);
}


function buildUpdateEvidence(anchorBody, previousRoot) {
  const relation = {
    kappa: anchorBody.kappa,
    previousAid: anchorBody.prevAid,
    previousRoot: String(previousRoot),
    previousVersion: anchorBody.version - 1,
    newAid: computeAnchorAid(anchorBody),
    newRoot: anchorBody.root,
    newVersion: anchorBody.version,
  };
  return {
    ...relation,
    relationDigest: taggedDigest('TARMS-UPDATE-v1\0', relation),
  };
}


function rootFor(material) {
  return crypto.createHash('sha256').update(`root|${material}`).digest('hex');
}


function uriHashFor(material) {
  return crypto.createHash('sha256').update(`uri|${material}`).digest('hex');
}


function appendJsonl(output, row) {
  fs.appendFileSync(output, `${JSON.stringify(row)}\n`, 'utf8');
}


async function setupLatest(adapter, kappa, runId, recordCount) {
  const anchorBody = buildAnchorBody({
    kappa,
    version: 1,
    root: rootFor(`${runId}|setup|root`),
    prevAid: '',
    recordCount,
    uriHash: uriHashFor(`${runId}|setup|uri`),
    createdAt: new Date().toISOString(),
  });
  const evidence = buildUpdateEvidence(anchorBody, '');
  const submitted = await adapter.submit('InstallAnchorCAS', [
    JSON.stringify(anchorBody),
    '',
    '0',
    JSON.stringify(evidence),
  ]);
  const result = JSON.parse(submitted.result.toString('utf8'));
  const expectedAid = computeAnchorAid(anchorBody);
  if (result.aid !== expectedAid) {
    throw new Error(`ANCHOR_MISMATCH: expected content-derived aid ${expectedAid}`);
  }
  return result.aid;
}


function operationMetadata(options, clientId, operation, attempt) {
  return {
    runId: options.runId,
    workload: options.workload,
    clientId,
    operation,
    attempt,
    provenance: 'measured_fabric',
  };
}


async function observe(adapter, options, clientId, operation, attempt, action, clock, record) {
  const outcome = await measureOperation({
    metadata: operationMetadata(options, clientId, operation, attempt),
    action,
    clock,
  });
  if (record) {
    appendJsonl(options.output, outcome.row);
  }
  return outcome;
}


async function runForDuration(durationSeconds, concurrency, operation) {
  const stopAt = Date.now() + durationSeconds * 1_000;
  const workers = Array.from({ length: concurrency }, (_, worker) =>
    (async () => {
      let sequence = 0;
      while (Date.now() < stopAt) {
        await operation(worker, sequence);
        sequence += 1;
      }
      return sequence;
    })()
  );
  return Promise.all(workers);
}


async function runPhase(adapter, options, durationSeconds, record, phase) {
  const origin = process.hrtime.bigint();
  const clock = () => process.hrtime.bigint() - origin;
  return runForDuration(durationSeconds, options.concurrency, async (worker, sequence) => {
    const clientId = `client-${String(worker + 1).padStart(2, '0')}`;
    const phasedRun = `${options.runId}|${phase}`;

    if (options.workload === 'anchor_submit' || options.workload === 'concurrency') {
      const kappa = `${clientId}|${phase}|${sequence}`;
      const anchorBody = buildAnchorBody({
        kappa,
        version: 1,
        root: rootFor(`${phasedRun}|${clientId}|${sequence}|root`),
        prevAid: '',
        recordCount: options.recordCount,
        uriHash: uriHashFor(`${phasedRun}|${clientId}|${sequence}|uri`),
        createdAt: new Date().toISOString(),
      });
      await observe(
        adapter,
        options,
        clientId,
        'InstallAnchorCAS',
        1,
        () => adapter.submit('InstallAnchorCAS', [
          JSON.stringify(anchorBody),
          '',
          '0',
          JSON.stringify(buildUpdateEvidence(anchorBody, '')),
        ]),
        clock,
        record
      );
      return;
    }

    if (options.workload === 'query') {
      const operation = sequence % 2 === 0 ? 'ReadLatest' : 'ReadAnchor';
      const args = operation === 'ReadLatest'
        ? [options.hotKey]
        : [options.queryAid];
      await observe(
        adapter, options, clientId, operation, 1,
        () => adapter.evaluate(operation, args), clock, record
      );
      return;
    }

    for (let attempt = 1; attempt <= options.maxAttempts; attempt += 1) {
      const read = await observe(
        adapter,
        options,
        clientId,
        'ReadLatest',
        attempt,
        () => adapter.evaluate('ReadLatest', [options.hotKey]),
        clock,
        record
      );
      if (read.error) return;
      const latest = JSON.parse(read.result.toString('utf8'));
      const nextVersion = latest.version + 1;
      const anchorBody = buildAnchorBody({
        kappa: options.hotKey,
        version: nextVersion,
        root: rootFor(`${phasedRun}|${clientId}|${sequence}|${attempt}|root`),
        prevAid: latest.aid,
        recordCount: options.recordCount,
        uriHash: uriHashFor(`${phasedRun}|${clientId}|${sequence}|${attempt}|uri`),
        createdAt: new Date().toISOString(),
      });
      const cas = await observe(
        adapter,
        options,
        clientId,
        'InstallAnchorCAS',
        attempt,
        () => adapter.submit('InstallAnchorCAS', [
          JSON.stringify(anchorBody),
          latest.aid,
          String(latest.version),
          JSON.stringify(buildUpdateEvidence(anchorBody, latest.root)),
        ]),
        clock,
        record
      );
      if (!cas.error) return;
      if (!['CAS_CONFLICT', 'MVCC_READ_CONFLICT'].includes(cas.row.error_class)) return;
    }
  });
}


function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}


async function runSelected(adapter, options, environment = process.env) {
  fs.mkdirSync(path.dirname(options.output), { recursive: true });
  fs.mkdirSync(path.dirname(options.manifest), { recursive: true });
  fs.writeFileSync(options.output, '', 'utf8');

  if (options.workload === 'query') {
    options.queryAid = await setupLatest(adapter, options.hotKey, options.runId, options.recordCount);
  } else if (options.workload === 'hot_key_cas') {
    await setupLatest(adapter, options.hotKey, options.runId, options.recordCount);
  }
  if (options.warmupSeconds > 0) {
    await runPhase(adapter, options, options.warmupSeconds, false, 'warmup');
  }
  const counts = await runPhase(adapter, options, options.durationSeconds, true, 'measured');
  let finalLatest = null;
  if (options.workload === 'hot_key_cas') {
    finalLatest = JSON.parse(
      (await adapter.evaluate('ReadLatest', [options.hotKey])).result.toString('utf8')
    );
  }
  const manifest = {
    schema_version: '1.0',
    experiment: 'fabric',
    provenance: 'measured_fabric',
    run_id: options.runId,
    created_at: new Date().toISOString(),
    environment: {
      node: process.version,
      fabric_version: environment.FABRIC_VERSION || '2.5.16',
      fabric_ca_version: environment.FABRIC_CA_VERSION || '1.5.17',
      network_id: environment.FABRIC_NETWORK_ID || 'tarms-two-org-test-network',
      channel: environment.FABRIC_CHANNEL,
      chaincode: environment.FABRIC_CHAINCODE,
      workload: options.workload,
      duration_seconds: options.durationSeconds,
      warmup_seconds: options.warmupSeconds,
      concurrency: options.concurrency,
      record_count: options.recordCount,
      max_attempts: options.maxAttempts,
      completed_logical_operations: counts.reduce((a, b) => a + b, 0),
      final_latest: finalLatest,
    },
    artifacts: { [path.basename(options.output)]: sha256File(options.output) },
  };
  fs.writeFileSync(options.manifest, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  return manifest;
}


module.exports = {
  buildAnchorBody,
  buildUpdateEvidence,
  computeAnchorAid,
  parseArgs,
  runSelected,
  setupLatest,
};
