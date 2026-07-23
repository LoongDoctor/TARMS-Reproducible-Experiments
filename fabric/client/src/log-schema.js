'use strict';


const REQUIRED_FIELDS = Object.freeze([
  'run_id',
  'workload',
  'client_id',
  'operation',
  'start_ns',
  'end_ns',
  'duration_ns',
  'txid',
  'commit_status',
  'block_number',
  'attempt',
  'error_class',
  'payload_bytes',
  'provenance',
]);


function safeNumber(value, name, minimum = 0) {
  const numeric = typeof value === 'bigint' ? Number(value) : Number(value);
  if (!Number.isSafeInteger(numeric) || numeric < minimum) {
    throw new Error(`${name} must be a safe integer >= ${minimum}`);
  }
  return numeric;
}


function validateObservation(row, options = {}) {
  for (const field of REQUIRED_FIELDS) {
    if (!Object.hasOwn(row, field)) {
      throw new Error(`missing required field: ${field}`);
    }
  }
  const start = safeNumber(row.start_ns, 'start_ns');
  const end = safeNumber(row.end_ns, 'end_ns');
  if (end <= start) {
    throw new Error('end_ns must be greater than start_ns');
  }
  if (safeNumber(row.duration_ns, 'duration_ns', 1) !== end - start) {
    throw new Error('duration_ns must equal end_ns minus start_ns');
  }
  safeNumber(row.block_number, 'block_number', -1);
  safeNumber(row.attempt, 'attempt', 1);
  safeNumber(row.payload_bytes, 'payload_bytes');
  if (options.submission && row.provenance !== 'measured_fabric') {
    throw new Error(
      `Fabric submission evidence cannot use provenance ${JSON.stringify(row.provenance)}`
    );
  }
  return row;
}


function createObservation(input) {
  const start = safeNumber(input.startNs, 'start_ns');
  const end = safeNumber(input.endNs, 'end_ns');
  const row = {
    run_id: String(input.runId),
    workload: String(input.workload),
    client_id: String(input.clientId),
    operation: String(input.operation),
    start_ns: start,
    end_ns: end,
    duration_ns: end - start,
    txid: String(input.txid),
    commit_status: String(input.commitStatus),
    block_number: safeNumber(input.blockNumber, 'block_number', -1),
    attempt: safeNumber(input.attempt, 'attempt', 1),
    error_class: String(input.errorClass),
    payload_bytes: safeNumber(input.payloadBytes, 'payload_bytes'),
    provenance: String(input.provenance),
  };
  return validateObservation(row);
}


module.exports = { REQUIRED_FIELDS, createObservation, validateObservation };
