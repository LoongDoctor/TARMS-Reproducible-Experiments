# AAMOS-00 source data

The original AAMOS-00 files are not redistributed here. Download the public
anonymized release from the University of Edinburgh DataShare:

- DOI: <https://doi.org/10.7488/ds/3775>
- Dataset: *AAMOS-00 Study: Predicting Asthma Attacks Using Connected Mobile
  Devices and Machine Learning, 2021–2022*

Place the following five files in `data/aamos00/` without renaming them:

| File | SHA-256 |
|---|---|
| `aamos00_data_dictionary.xlsx` | `0d50002843b80b75db2a764ffd0e0a8139f881c1cf84b2ca6f8956ab5884bcbc` |
| `anonym_aamos00_dailyquestionnaire.csv` | `8133aeba38c2bb5db0027731e64c09f7c2436e55fa25d845665786db88820f24` |
| `anonym_aamos00_peakflow.csv` | `0b211e61d4aaa4613d25e95777af03ff535767b744ef79f38ef2722d2374ba83` |
| `anonym_aamos00_smartinhaler.csv` | `925ea383539d14cefb0f92d52c1a254c1316271185f04f07de4c08281414dd9a` |
| `anonym_aamos00_weeklyquestionnaire.csv` | `697e6d21f3d61145fec881345d9a6682ac9e95f27d4da2ee0a2dc63ec55f0eba` |

The fixed submission configuration is `config/aamos00_derivation.yaml`.
Submission-profile execution rejects a source inventory or derivation
configuration that does not match the recorded contract.

AAMOS-00 supplies anonymized respiratory-monitoring payloads and
participant-day clusters only. Device identities, keys, signatures, device and
binding states, admission state, Merkle objects, latest pointers,
authorization contexts, and all integrity-scenario labels are synthetic TARMS
experiment metadata.
