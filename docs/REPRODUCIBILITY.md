# TARMS Experiment Reproducibility Guide

## 1. Scope

This guide covers four reproducible layers:

1. Python local microbenchmarks;
2. deterministic late-update conformance cases;
3. component-level signature, acceptance, Merkle and version-CAS cases;
4. figure and source-data generation.

The Fabric chaincode and Gateway client can be unit-tested locally. A separate optional section describes how to run the Fabric 2.5 test network; no real-network output from that path is bundled.

## 2. Supported environment

The measured reference run used:

- CPython 3.12.13;
- `cryptography==46.0.0`;
- `matplotlib==3.10.8`;
- `numpy==2.3.5`;
- `pandas==2.2.3`;
- `SciencePlots==2.1.1`;
- Linux 6.12.13 on an AMD EPYC 9V74 environment.

The code targets Python 3.12 and Node.js 20 or newer. Use a clean virtual environment.

### macOS or Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make install
```

If `python3.12` is unavailable on macOS with Homebrew:

```bash
brew install python@3.12 node@20 make
```

Windows users should run the same commands inside WSL2.

## 3. Verify the released evidence

Run all tests, manifest checks, shell syntax checks and figure generation:

```bash
make verify
```

The Python suite uses `unittest`. The JavaScript packages use the Node.js built-in test runner. The data gate recomputes SHA-256 for every raw, processed and environment artifact listed by the three measured run manifests.

To run only the evidence check:

```bash
make data
```

To verify the full release tree:

```bash
sha256sum -c MANIFEST.sha256
```

On macOS:

```bash
shasum -a 256 -c MANIFEST.sha256
```

## 4. Regenerate the bundled figures

```bash
make figures
```

Outputs are written to `results/figures/submission/`:

- `fig_03_python_benchmarks.{pdf,png}`;
- `fig_04_component_conformance.{pdf,png}`;
- `fig_05_window_tradeoff.{pdf,png}`;
- one `_source_data.csv` file per figure.

Formal Python and component figures require a `measured` run manifest. The plotting gate rejects fixture provenance.
PDF export omits wall-clock creation and modification metadata, so repeated
figure generation with the locked environment is byte-reproducible.

## 5. Run a quick smoke experiment

Use a separate output directory:

```bash
python scripts/run_python_benchmarks.py \
  --profile quick \
  --output-root reproduced_results

python scripts/run_conformance.py \
  --repetitions 10 \
  --output-root reproduced_results

python scripts/run_component_conformance.py \
  --repetitions 10 \
  --output-root reproduced_results

python scripts/make_figures.py \
  --figure python \
  --mode submission \
  --results reproduced_results

python scripts/make_figures.py \
  --figure component \
  --mode submission \
  --results reproduced_results

python scripts/make_figures.py \
  --figure window \
  --mode submission \
  --results reproduced_results
```

The quick profile is suitable for installation checks. Do not compare its small repetition count with the bundled full run.

## 6. Re-run the full experiment matrix

```bash
make benchmark
make conformance
make figures-rerun
```

Equivalent commands are:

```bash
python scripts/run_python_benchmarks.py \
  --profile submission \
  --output-root reproduced_results

python scripts/run_conformance.py \
  --repetitions 200 \
  --output-root reproduced_results

python scripts/run_component_conformance.py \
  --repetitions 200 \
  --seed 20260722 \
  --output-root reproduced_results
```

The full Python run uses batch sizes `16, 64, 256, 1024, 2048, 4096`, 20 warmups and 200 measured repetitions for each of six stages. The two conformance runs use 200 executions per prespecified case. Run IDs are generated in UTC and do not overwrite the bundled release.

## 7. Interpret outputs correctly

### Python microbenchmarks

`python_microbenchmark.csv` is the raw observation table. `python_microbenchmark_summary.csv` reports median, quartiles, P95, bootstrap 95% confidence intervals for the median and median records per second.

`signature_admission_batch` includes Ed25519 verification plus the in-memory `(device, key version, boot, counter)` acceptance state machine. It does not include a Fabric transaction, network communication, persistent storage or the complete current-state verification path.

### Conformance cases

The late-update and component tables report whether constructed cases match their prespecified accepted/rejected outcomes. Values such as `200/200` are deterministic implementation-conformance counts, not estimates of real-world attack prevalence or detection performance.

### Payload/window model

The model uses:

- 384 B per encoded raw event and signature;
- 48 B per per-record digest/key-index entry;
- 614 B for the modeled steady-state `anchor + latest` application payload;
- uniform arrivals within each anchoring window.

These are explicit application-layer assumptions, not measured Fabric ledger size or a deployment prescription.

## 8. Optional Fabric test-network run

Requirements:

- Docker;
- Hyperledger Fabric 2.5.16 binaries and images;
- Fabric CA 1.5.17;
- an official `fabric-samples` checkout compatible with Fabric 2.5.16.

```bash
export FABRIC_SAMPLES_DIR=/absolute/path/to/fabric-samples
bash fabric/network/bootstrap.sh
PROFILE=smoke bash fabric/network/run_experiments.sh
bash fabric/network/teardown.sh
```

For the full network matrix:

```bash
PROFILE=submission bash fabric/network/run_experiments.sh
```

Network observations are written under `results/raw/fabric/`, which is ignored by Git. The bootstrap creates `fabric/network/.env.fabric` containing local certificate paths; this file is also ignored and must never be committed.

The current release validates Fabric code with unit tests and shell syntax checks. Do not report Fabric latency, TPS or conflict-rate results unless the network run is completed, its manifests say `measured_fabric`, and its raw logs are independently reviewed.

## 9. Common issues

### `ModuleNotFoundError: scienceplots`

Activate the virtual environment and reinstall the lock file:

```bash
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
```

### Node dependencies are missing

```bash
npm --prefix fabric/chaincode ci --ignore-scripts --no-audit --no-fund
npm --prefix fabric/client ci --ignore-scripts --no-audit --no-fund
```

### Figure generation selects the wrong run

Pass an explicit run directory:

```bash
python scripts/make_figures.py \
  --figure python \
  --python-run reproduced_results/raw/python/<run-id> \
  --results reproduced_results
```

### Absolute timings differ

This is expected across hardware and operating systems. Preserve the same dependency versions, seed, batch sizes, warmups and repetitions, then report the new environment recorded in `environment.json`.
