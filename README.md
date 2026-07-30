# TARMS reproducible experiments

[中文说明](README_zh.md)

This repository candidate contains the code and publishable evidence for the
TARMS Scientific Reports R4 manuscript. It includes local Python measurements,
late-update and component-conformance cases, a controlled AAMOS-00
protocol-integrity experiment, plotting code, tests, and a Hyperledger Fabric
implementation scaffold.

## Evidence included

| Evidence layer | Reference run | Interpretation |
|---|---|---|
| Python microbenchmarks | `python-20260723T020649Z` | Local Python primitive and algorithm timing |
| Late-update conformance | `conformance-20260723T020659Z` | Constructed root-reconstruction cases |
| Component conformance | `components-20260723T020700Z` | Signature, `AcceptOnce`, Merkle, and latest-CAS cases |
| AAMOS-00 controlled experiment | Formal run declared by `public_release_manifest.json` | Public anonymized payloads with synthetic protocol metadata and constructed integrity scenarios |
| Payload/window model | Figure source data | Explicit application-payload and uniform-arrival model |
| Fabric scaffold | Unit-tested source | Interface and transaction semantics only |

The AAMOS formal design used 22 participants, 1,582 eligible
daily-questionnaire participant-days, 20 fixed seeds, 14 constructed-invalid
scenarios, seven boundary controls, 12 capability configurations, four
nonzero requested injection rates, and 2,000 crossed seed–participant
bootstrap repetitions. It generated 2,556,960 configuration-evaluation rows;
37,920 permanent-omission rows had no returned record, leaving 2,519,040 rows
with a local experimental outcome.

These are controlled implementation-conformance results. They are not
clinical accuracy, real-world attack-detection, event-completeness, or
deployed-blockchain performance estimates. No real-network Fabric performance
result is included.

## Install and verify

Requirements:

- Python 3.12
- Node.js 20 or newer
- GNU Make and a POSIX shell

For development and release production:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make install
mkdir -p ../releases/v6
make release-test-report REPORT=../releases/v6/test-report.json
```

The safe release order is:

```text
install -> release-test-report -> freeze source -> formal run ->
reproduce into isolated output -> seal new release -> read-only verify
```

After the machine test report is green, freeze the controlled source before
starting the formal run. Reproduce figures only into an explicit isolated
directory:

```bash
make reproduce-figures OUTPUT_DIR=../releases/v6/figures
make seal-release \
  RELEASE_DIR=../releases/v6/public-release \
  RUN_DIR=../releases/v6/reproduced_results/processed/aamos/aamos-submission-20260729-v6-local \
  SNAPSHOT=../releases/v6/controlled-source.zip \
  TEST_REPORT=../releases/v6/test-report.json \
  FIGURE_DIR=../releases/v6/figures
```

Create manifests and the public package only through `make seal-release` with
explicit release, run, snapshot, test-report, and isolated figure paths.

A clean public extraction has two deliberate verification phases. Before
installing anything into the extracted tree, verify the sealed artifacts and
complete tree:

```bash
make verify-public verify-tree
```

Then install locked dependencies and run the executable test gates:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
make install
make test-python test-node verify-shell
```

Dependency installation creates unsealed `.venv` or `node_modules` runtime
directories. Do not rerun the complete-tree gate on that modified working
copy; extract the ZIP again when a fresh integrity check is required. The
release builder enforces this same integrity-before-install order in a
temporary fresh extraction.

## AAMOS-00 source and reproduction

The original AAMOS-00 files are intentionally not redistributed. Download DOI
[`10.7488/ds/3775`](https://doi.org/10.7488/ds/3775), then follow
[`data/README.md`](data/README.md) to place and hash-check the five files used
by the frozen derivation.

A quick one-seed preview is:

```bash
PYTHONPATH=src python scripts/run_aamos_standard_enhanced.py \
  --source-dir data/aamos00 \
  --output-root reproduced_results \
  --profile preview \
  --rates 0.01 \
  --seeds 20260722 \
  --bootstrap-reps 80 \
  --run-id aamos-preview-local
```

The formal design can be rerun without overwriting the bundled reference
artifacts:

```bash
PYTHONPATH=src python scripts/run_aamos_standard_enhanced.py \
  --source-dir data/aamos00 \
  --output-root reproduced_results \
  --profile submission \
  --bootstrap-reps 2000 \
  --bootstrap-seed 20260722 \
  --run-id aamos-submission-local
```

On the reference environment the full run required about 21 minutes and
approximately 5 GiB peak resident memory. Large participant-keyed and
per-evaluation tables are regenerated locally; this candidate publishes only
aggregate statistics, figure source data, and cryptographic identities for
the omitted canonical artifacts.

`tests/fixtures/aamos_minimal.csv` is a 187-byte synthetic unit-test fixture.
It is retained only so the test suite is self-contained; the submission
provenance gate prevents it from producing a formal figure or result.

## Layout

```text
config/                       Frozen AAMOS derivation contract
data/                         Public-source download and hash instructions
docs/                         Evidence, reproduction, and claim boundaries
fabric/                       Chaincode, Gateway client, and network scripts
results/raw/                  Non-AAMOS measured observations and manifests
results/processed/            Summaries and publishable AAMOS aggregates
results/figures/submission/   PDF/PNG figures and source-data tables
scripts/                      Run, plot, and release-verification commands
src/tarms_experiments/        Python implementation
tests/                        Python and Fabric-scaffold tests
```

## License and citation

Code is released under the [Apache License 2.0](LICENSE). Citation metadata are
provided in [`CITATION.cff`](CITATION.cff).
