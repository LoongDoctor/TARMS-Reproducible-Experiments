#!/usr/bin/env node
'use strict';

const { connectGateway, loadConnectionMaterial } = require('./gateway');
const { parseArgs, runSelected } = require('./runner');


async function main() {
  const options = parseArgs(process.argv.slice(2));
  const adapter = connectGateway(loadConnectionMaterial(process.env));
  try {
    const manifest = await runSelected(adapter, options, process.env);
    process.stdout.write(`${JSON.stringify({
      run_id: manifest.run_id,
      workload: manifest.environment.workload,
      output: options.output,
      manifest: options.manifest,
    })}\n`);
  } finally {
    adapter.close();
  }
}


main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
