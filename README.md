# TARMS Reproducible Experiments

[中文说明](README_zh.md)

This repository contains the reproducible experiment artifacts for TARMS. It is intentionally limited to experiment source code, measured data, plotting code, generated figures, source-data tables, tests, and the Hyperledger Fabric implementation scaffold.

## Included evidence

| Evidence layer | Bundled run | Size | Interpretation |
|---|---|---:|---|
| Python microbenchmarks | `python-20260723T020649Z` | 7,200 observations | Local primitive and algorithm timing |
| Late-update conformance | `conformance-20260723T020659Z` | 1,200 executions | Deterministic root-reconstruction cases |
| Component conformance | `components-20260723T020700Z` | 2,200 executions | Signature, AcceptOnce, Merkle and latest-CAS cases |
| Payload/window model | Figure source data | 6 window settings | Explicit application-payload and uniform-arrival model |
| Fabric implementation | Unit-tested source | 6 chaincode + 9 client tests | Interface and transaction semantics only |

The repository does **not** contain real-network Fabric measurements. Python throughput is not Fabric TPS or end-to-end system throughput. The bundled conformance cases are constructed rule checks, not clinical performance estimates.

## Quick verification

Requirements:

- Python 3.12
- Node.js 20 or newer
- GNU Make

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make install
make verify
```

Expected verification totals:

- 49 Python tests
- 6 Fabric chaincode tests
- 9 Fabric Gateway client tests
- 7 measured artifacts matched to their run-manifest SHA-256 values
- 3 figure families regenerated from the bundled data/model

For Windows, use WSL2 or an equivalent POSIX shell. Detailed setup, rerun commands, output interpretation and the optional Fabric network path are in [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md).

## Repository layout

```text
fabric/                       Chaincode, Gateway client and network scripts
scripts/                      Benchmark, conformance, plotting and hash checks
src/tarms_experiments/        Python experiment implementation
tests/                        Python and static scaffold tests
results/raw/                  Latest measured observation tables and manifests
results/processed/            Precomputed summaries
results/figures/submission/   PDF/PNG figures and figure source-data CSV files
docs/                         Reproduction, evidence and upload documentation
```

## Reproducing the measured runs

The default rerun directory is `reproduced_results/`, which is ignored by Git:

```bash
make benchmark
make conformance
make figures-rerun
```

These commands create new timestamped run IDs. Timing values are expected to vary by CPU, operating system, Python build and background load. Compare schema, case outcomes and scaling behavior before comparing absolute timing.

## Integrity and evidence boundaries

```bash
make data
```

This verifies every file named in the three bundled run manifests. `MANIFEST.sha256` additionally covers the complete release tree. See [docs/EVIDENCE_REGISTER.md](docs/EVIDENCE_REGISTER.md) for exact run IDs, counts and approved interpretations.

## License and citation

Released under the [Apache License 2.0](LICENSE). Citation metadata are provided in [CITATION.cff](CITATION.cff).
