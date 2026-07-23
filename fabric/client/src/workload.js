'use strict';

const { createObservation } = require('./log-schema');


function classifyError(error) {
  const message = String(error && error.message ? error.message : error);
  const classes = [
    'CAS_CONFLICT',
    'VERSION_CONTINUITY',
    'COUNTER_CONFLICT',
    'UPDATE_EVIDENCE',
    'ACCESS_DENIED',
    'ANCHOR_EXISTS',
    'ANCHOR_MISMATCH',
    'NOT_FOUND',
    'MVCC_READ_CONFLICT',
    'ENDORSEMENT_POLICY_FAILURE',
  ];
  return classes.find((label) => message.includes(label)) || 'UNCLASSIFIED';
}


async function measureOperation({ metadata, action, clock = process.hrtime.bigint }) {
  const startNs = clock();
  try {
    const result = await action();
    const endNs = clock();
    return {
      row: createObservation({
        ...metadata,
        startNs,
        endNs,
        txid: result.txid || '',
        commitStatus: result.commitStatus || 'VALID',
        blockNumber: result.blockNumber ?? -1n,
        payloadBytes: result.payloadBytes ?? 0,
        errorClass: '',
      }),
      result: result.result,
      error: null,
    };
  } catch (error) {
    const endNs = clock();
    return {
      row: createObservation({
        ...metadata,
        startNs,
        endNs,
        txid: error.transactionId || '',
        commitStatus: 'ERROR',
        blockNumber: -1n,
        payloadBytes: error.payloadBytes || 0,
        errorClass: classifyError(error),
      }),
      result: null,
      error,
    };
  }
}


module.exports = { classifyError, measureOperation };
