# TARMS Experiment Evidence Register

## Bundled runs

| Layer | Run ID | Raw observations | Provenance |
|---|---|---:|---|
| Python local microbenchmarks | `python-20260723T020649Z` | 7,200 | `measured` |
| Late-update root conformance | `conformance-20260723T020659Z` | 1,200 | `measured` |
| Component conformance | `components-20260723T020700Z` | 2,200 | `measured` |

## Run-manifest artifact hashes

### `python-20260723T020649Z`

| Artifact | SHA-256 |
|---|---|
| `raw/python/python-20260723T020649Z/python_microbenchmark.csv` | `ea8826c78d1ac213affc1fdb3ac35f1cae901550f8dce6729727983028de4f92` |
| `processed/python/python-20260723T020649Z/python_microbenchmark_summary.csv` | `e58abc77856d7cb625ff9eecd1ab0fcc1296c35c018fcdfae544fe4da4fb563b` |
| `raw/python/python-20260723T020649Z/environment.json` | `5825bbdb76b1b4c8cfa40685db61cc6de4052b44e42eb9e67350b14b5ee7722d` |

### `conformance-20260723T020659Z`

| Artifact | SHA-256 |
|---|---|
| `raw/python_conformance/conformance-20260723T020659Z/late_update_conformance.csv` | `b2515ffe070136ba04d67931f520cce16962a0507a67d1c93ca56521fba9472c` |
| `processed/python_conformance/conformance-20260723T020659Z/late_update_conformance_summary.csv` | `b37e9f60a05392d58dfc48f19934dafce1baa01817dfe71efa778ecc0f26ec8c` |

### `components-20260723T020700Z`

| Artifact | SHA-256 |
|---|---|
| `raw/python_components/components-20260723T020700Z/component_conformance.csv` | `4d87522b69f150f9e99f846564d87bc4a5571dc30467104ee7571c13adcce1bb` |
| `processed/python_components/components-20260723T020700Z/component_conformance_summary.csv` | `a9c424b586dc27173b8f28b4fec3646eed63f259ea3b036bfcf0e0e60d7acd30` |

Run:

```bash
python scripts/verify_run_manifests.py
```

to recompute all seven values directly from the bundled files.

## Figure provenance

| Figure family | Input | Status |
|---|---|---|
| Python benchmarks | Raw and processed Python measured run | Measured |
| Component conformance | Raw component measured run | Measured constructed cases |
| Window tradeoff | Explicit 614 B payload and uniform-arrival assumptions | Modeled |

Each figure family includes PDF, PNG and a source-data CSV.

## Claim boundaries

- Python timing results describe local computation on the recorded environment.
- `signature_admission_batch` is signature verification plus in-memory admission, not full protocol verification or ledger throughput.
- Late-update results describe local reconstruction and deterministic conformance cases.
- Component proportions describe agreement with prespecified outcomes for constructed cases.
- The 614 B value is an application-payload model for one steady-state anchor plus one latest pointer at the stated schema.
- Fabric source is unit-tested; no real-network Fabric performance dataset is bundled.
- No patient-level dataset or clinical performance result is included.

